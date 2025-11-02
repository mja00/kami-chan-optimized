# flake8: noqa: E501
import os
import sys
from pathlib import Path
import tomllib
import tomli_w
import subprocess
from git import Repo
import re
import argparse

# Cache the packwiz command based on OS
PACKWIZ_CMD = "./packwiz.exe" if sys.platform == "win32" else "./packwiz"
MODS_DIR = Path("mods")
PACK_TOML = Path("pack.toml")


def parse_semver(version_string):
    """Parse semantic version string and return major, minor, patch components."""
    match = re.match(r'(\d+)\.(\d+)\.(\d+)', version_string)
    if match:
        return int(match.group(1)), int(match.group(2)), int(match.group(3))
    raise ValueError(f"Invalid semantic version format: {version_string}")


def bump_minor_version(version_string):
    """Bump the minor version of a semantic version string."""
    major, minor, patch = parse_semver(version_string)
    return f"{major}.{minor + 1}.0"


def get_outdated_mods():
    """Get all outdated mod files from the mods directory."""
    outdated_mods = {}
    for mod_file in MODS_DIR.iterdir():
        if mod_file.name.endswith(".outdated"):
            with mod_file.open("rb") as f:
                outdated_mods[mod_file.name] = tomllib.load(f)
    return outdated_mods


def refresh_index():
    """Refresh the packwiz index."""
    try:
        subprocess.run([PACKWIZ_CMD, "refresh"], capture_output=True, check=True)
    except subprocess.CalledProcessError:
        pass  # Ignore errors in refresh


def attempt_update(mod: str, info: dict):
    """Attempt to update a single mod. Returns True if successful, False otherwise."""
    outdated_file = MODS_DIR / mod
    updated_file = MODS_DIR / mod.removesuffix(".outdated")
    
    # Remove the .outdated extension
    outdated_file.rename(updated_file)
    
    def rollback():
        """Rollback the rename operation."""
        updated_file.rename(outdated_file)
        refresh_index()
    
    try:
        refresh_index()
        # Extract mod name by removing .pw.toml.outdated or .pw.toml suffix
        mod_name = mod.removesuffix(".outdated").removesuffix(".pw.toml")
        print(f"Updating {info['name']}")
        
        result = subprocess.run(
            [PACKWIZ_CMD, "update", mod_name, "-y"],
            capture_output=True,
            text=True,
            check=False
        )
        
        filename = info["filename"]
        # Check if the filename appears in the output (indicating successful update)
        if filename in result.stdout:
            print(f"Successfully updated {info['name']}")
            return True
        else:
            print(f"Failed to update {info['name']}")
            if result.stdout:
                # Print the second-to-last line (usually contains error info)
                lines = result.stdout.rstrip().split("\n")
                if len(lines) >= 2:
                    print(lines[-2])
            rollback()
            return False
    except Exception as e:
        print(f"Failed to update {info['name']}: {e}")
        rollback()
        raise


def read_pack_toml():
    """Read and parse pack.toml file."""
    with PACK_TOML.open("rb") as f:
        return tomllib.load(f)


def update_pack_version():
    """Update the version in pack.toml by bumping the minor version."""
    pack_data = read_pack_toml()
    current_version = pack_data["version"]
    new_version = bump_minor_version(current_version)
    print(f"Bumping version from {current_version} to {new_version}")
    
    pack_data["version"] = new_version
    
    with PACK_TOML.open("wb") as f:
        tomli_w.dump(pack_data, f)
    
    return new_version


def main(args):
    updated = 0
    outdated_mods = get_outdated_mods()
    updated_mods = []
    print(f"Found {len(outdated_mods)} outdated mods")

    removed_outdated_files = []
    # Loop through the outdated mods getting the key and value
    for mod, info in outdated_mods.items():
        success = attempt_update(mod, info)
        if success:
            updated += 1
            mod_name = mod.removesuffix(".pw.toml.outdated").removesuffix(".pw.toml")
            updated_mods.append(mod_name)
            removed_outdated_files.append(MODS_DIR / mod)
    
    print(f"Updated {updated} outdated mods")

    # Then just run packwiz update --all -y for good measure
    try:
        results = subprocess.run(
            [PACKWIZ_CMD, "update", "--all", "-y"],
            capture_output=True,
            text=True,
            check=False
        )
        # Parse how many mods were updated. These are lines that'll be *.jar -> *.jar
        packwiz_updated_mods = [
            line.split(":")[0].strip()
            for line in results.stdout.split("\n")
            if "->" in line
        ]
        print(f"Updated {len(packwiz_updated_mods)} mods")
        updated += len(packwiz_updated_mods)
        updated_mods.extend(packwiz_updated_mods)
    except Exception as e:
        print(f"Error during --all update: {e}")
        # Don't clear updated_mods - we still want to commit what we already updated
    
    print(f"Total mods updated: {updated}")
    print(f"Updated mods: {updated_mods}")
    
    # If we updated more than 0, create a commit and bump version
    if args.skip_commit:
        print("Skipping commit and tag creation")
        return
    if updated > 0:
        new_version = update_pack_version()
        repo = Repo(Path.cwd())
        # Add any changed files (both untracked and modified)
        repo.index.add(repo.untracked_files)
        # Add modified files by getting their paths from the diff
        modified_files = [
            Path(diff.a_path)
            for diff in repo.index.diff(None)
            if Path(diff.a_path).exists()
        ]
        if modified_files:
            repo.index.add([str(f) for f in modified_files])
        # Remove deleted .outdated files from git index
        if removed_outdated_files:
            actually_removed = [f for f in removed_outdated_files if not f.exists()]
            if actually_removed:
                repo.index.remove([str(f) for f in actually_removed], working_tree=True)

        # Create a detailed commit message with the list of updated mods
        mod_list = "\n".join(f"- {mod}" for mod in updated_mods)
        commit_message = f"chore: updated {updated} mods\n\nUpdated mods:\n{mod_list}\n"
        
        # Commit the changes with detailed mod list
        commit = repo.index.commit(commit_message)
        
        # Create a tag with the new version
        try:
            tag = repo.create_tag(new_version, ref=commit, message=f"Release version {new_version}")
            print(f"Created tag: {new_version}")
        except Exception as e:
            print(f"Warning: Could not create tag {new_version}: {e}")
        
        # Ask if user wants to push the commit and tag
        while True:
            push_choice = input("\nDo you want to push the commit and tag to origin? (y/n): ").lower().strip()
            if push_choice in ['y', 'yes']:
                try:
                    print("Pushing commit to origin...")
                    repo.remotes.origin.push()
                    print("Pushing tag to origin...")
                    repo.remotes.origin.push(tag)
                    print("Successfully pushed commit and tag to origin!")
                except Exception as e:
                    print(f"Error pushing to origin: {e}")
                break
            elif push_choice in ['n', 'no']:
                print("Skipping push to origin.")
                break
            else:
                print("Please enter 'y' or 'n'.")
    print("Done!")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-commit", action="store_true", help="Skip committing the changes")
    args = parser.parse_args()
    main(args)

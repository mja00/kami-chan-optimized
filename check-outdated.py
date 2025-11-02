# flake8: noqa: E501
import os
import sys
import tomllib
import tomli_w
import subprocess
from git import Repo
import re
import argparse


def get_packwiz_command():
    """Return the appropriate packwiz command based on the operating system."""
    if sys.platform == "win32":
        return "./packwiz.exe"
    else:
        return "./packwiz"


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
    outdated_mods = {}
    for mod in os.listdir("mods"):
        if mod.endswith(".outdated"):
            # Parse the toml file
            with open(os.path.join("mods", mod), "rb") as f:
                outdated_mods[mod] = tomllib.load(f)
    return outdated_mods


def refresh_index():
    try:
        subprocess.run([get_packwiz_command(), "refresh"], capture_output=True, check=True)
    except subprocess.CalledProcessError:
        pass  # Ignore errors in refresh


def attempt_update(mod: str, info: dict):
    # Remove the .outdated extension
    os.rename(os.path.join("mods", mod), os.path.join("mods", mod.replace(".outdated", "")))
    try:
        # First run ./packwiz.exe refresh to update its index
        refresh_index()
        mod_name = mod.split(".pw.toml")[0]
        print(f"Updating {info['name']}")
        # We want to run ./packwiz.exe update with the mod name
        # We also want to capture the output to determine if it was successful
        result = subprocess.run([get_packwiz_command(), "update", mod_name, "-y"], capture_output=True, text=True, check=False)
        # print(result.stdout)
        filename = info["filename"]
        # A common output of this is:
        # Updates found:
        # Iris Shaders: iris-fabric-1.8.8+mc1.21.4.jar -> iris-fabric-1.8.9+mc1.21.5.jar
        # Mod Menu: modmenu-13.0.3.jar -> modmenu-14.0.0-rc.1.jar
        # Sodium: sodium-fabric-0.6.10+mc1.21.4.jar -> sodium-fabric-0.6.11+mc1.21.5.jar
        # Fabric API: fabric-api-0.119.2+1.21.4.jar -> fabric-api-0.119.5+1.21.5.jar
        # YetAnotherConfigLib: yet_another_config_lib_v3-3.6.6+1.21.4-fabric.jar -> yet_another_config_lib_v3-3.6.6+1.21.5-fabric.jar
        # We want to check if the filename is anywhere in the output
        if filename in result.stdout:
            print(f"Successfully updated {info['name']}")
            return True
        else:
            print(f"Failed to update {info['name']}")
            if result.stdout:
                print(result.stdout.split("\n")[-2])
            # We want to rename the file back to .outdated
            os.rename(os.path.join("mods", mod.replace(".outdated", "")), os.path.join("mods", mod))
            # Update the index again
            refresh_index()
            return False
    except Exception as e:
        print(f"Failed to update {info['name']}")
        # We want to rename the file back to .outdated
        os.rename(os.path.join("mods", mod.replace(".outdated", "")), os.path.join("mods", mod))
        # Update the index again
        refresh_index()
        raise e


def read_pack_toml():
    with open("pack.toml", "rb") as f:
        return tomllib.load(f)


def update_pack_version():
    """Update the version in pack.toml by bumping the minor version."""
    pack_data = read_pack_toml()
    current_version = pack_data["version"]
    new_version = bump_minor_version(current_version)
    print(f"Bumping version from {current_version} to {new_version}")
    
    # Update the version in the pack data
    pack_data["version"] = new_version
    
    # Write the updated pack.toml
    with open("pack.toml", "wb") as f:
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
            updated_mods.append(mod.replace(".pw.toml", ""))
            removed_outdated_files.append(os.path.join("mods", mod))  # Track the removed .outdated file
    
    print(f"Updated {updated} outdated mods")

    # Then just run ./packwiz.exe update --all -y for good measure
    try:
        results = subprocess.run([get_packwiz_command(), "update", "--all", "-y"], capture_output=True, text=True, check=False)
        # Parse how many mods were updated. These are lines that'll be *.jar -> *.jar
        packwiz_updated_mods = [line for line in results.stdout.split("\n") if "->" in line]
        print(f"Updated {len(packwiz_updated_mods)} mods")
        updated += len(packwiz_updated_mods)
        # Extend updated_mods with the packwiz_updated_mods
        # Although we want to split on : and grab the first part (this'll be the mods name)
        for mod in packwiz_updated_mods:
            updated_mods.append(mod.split(":")[0].strip())
    except Exception as e:
        print(f"Error during --all update: {e}")
        updated_mods = []
    
    print(f"Total mods updated: {updated}")
    print(f"Updated mods: {updated_mods}")
    
    # If we updated more than 0, create a commit and bump version
    if args.skip_commit:
        print("Skipping commit and tag creation")
        return
    if updated > 0:
        new_version = update_pack_version()
        # Get our current path
        current_path = os.getcwd()
        repo = Repo(current_path)
        # Add any changed files (both untracked and modified)
        repo.index.add(repo.untracked_files)
        # Add modified files by getting their paths from the diff
        modified_files = [diff.a_path for diff in repo.index.diff(None)]
        # Only add files that still exist
        modified_files = [f for f in modified_files if os.path.exists(f)]
        if modified_files:
            repo.index.add(modified_files)
        # Remove deleted .outdated files from git index
        if removed_outdated_files:
            actually_removed = [f for f in removed_outdated_files if not os.path.exists(f)]
            if actually_removed:
                repo.index.remove(actually_removed, working_tree=True)

        # Create a detailed commit message with the list of updated mods
        commit_message = f"chore: updated {updated} mods\n\nUpdated mods:\n"
        for mod in updated_mods:
            commit_message += f"- {mod.replace('.outdated', '')}\n"
        
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

import os
import subprocess
from github import Github, Auth, GithubException

# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
REPO_NAME = "gesture-controller"
REPO_DESCRIPTION = "Real-Time Windows Gesture Controller built with Streamlit, OpenCV, MediaPipe, and WebRTC."
IS_PRIVATE = False

# --------------------------------------------------------------------------
# Helper Functions
# --------------------------------------------------------------------------
def run_cmd(command: str) -> bool:
    """Executes shell commands and handles output."""
    result = subprocess.run(command, shell=True, text=True, capture_output=True)
    if result.returncode != 0:
        print(f"⚠️ Note/Warning during '{command}':")
        print(result.stderr.strip() or result.stdout.strip())
        return False
    else:
        if result.stdout.strip():
            print(result.stdout.strip())
        return True


def create_github_repo(token: str, repo_name: str, description: str, private: bool) -> str:
    """Creates a repository using GitHub API (Updated PyGithub Auth Syntax)."""
    print("Connecting to GitHub API...")
    
    # Modernized PyGithub Authentication Syntax
    auth = Auth.Token(token)
    g = Github(auth=auth)
    
    user = g.get_user()

    try:
        print(f"Creating repository '{repo_name}'...")
        repo = user.create_repo(
            name=repo_name,
            description=description,
            private=private,
            auto_init=False,
        )
        print(f"✅ Repository created successfully: {repo.html_url}")
        return repo.clone_url
    except GithubException as e:
        if e.status == 422:
            print(f"ℹ️ Repository '{repo_name}' already exists on GitHub. Retrieving existing repository...")
            repo = user.get_repo(repo_name)
            return repo.clone_url
        else:
            raise e


def push_to_github(clone_url: str, token: str):
    """Initializes local git repository, commits files, and pushes to remote."""
    print("\nInitializing local Git workflow...")

    # Embed token into clone URL for non-interactive CLI authentication
    authenticated_url = clone_url.replace("https://", f"https://{token}@")

    # 1. Initialize Git repository if missing
    if not os.path.exists(".git"):
        run_cmd("git init")

    # 2. Rename branch to main
    run_cmd("git branch -M main")

    # 3. Stage all local files
    run_cmd("git add .")

    # 4. Create initial commit
    run_cmd('git commit -m "Initial commit: Gesture Controller Streamlit App"')

    # 5. Configure remote URL
    remotes = subprocess.run("git remote", shell=True, text=True, capture_output=True).stdout
    if "origin" in remotes:
        run_cmd(f"git remote set-url origin {authenticated_url}")
    else:
        run_cmd(f"git remote add origin {authenticated_url}")

    # 6. Push to remote repository
    print("Pushing repository to GitHub main branch...")
    success = run_cmd("git push -u origin main")

    if success:
        print("\n🚀 Code successfully pushed to GitHub!")

# --------------------------------------------------------------------------
# Main Execution Entry Point
# --------------------------------------------------------------------------
if __name__ == "__main__":
    if not GITHUB_TOKEN:
        print("❌ Error: GITHUB_TOKEN environment variable is not set.")
        print("Set token in terminal before running: $env:GITHUB_TOKEN='your_token'")
    else:
        try:
            clone_url = create_github_repo(GITHUB_TOKEN, REPO_NAME, REPO_DESCRIPTION, IS_PRIVATE)
            push_to_github(clone_url, GITHUB_TOKEN)
        except Exception as err:
            print(f"❌ Automation failed: {err}")
from cli.exceptions import ResourceNotFoundError
from cli.utilities.packages import Package
from cli.utilities.utils import os_major_version
from utility.log import Log
from utility.utils import get_cephci_config

log = Log(__name__)


def setup_local_repos(ceph):
    # Get configuration details from `~/.cephci.yaml`
    configs = get_cephci_config()

    # Get distro version
    os_version = os_major_version(ceph)

    # Get local repositories
    repos = configs.get("repo")
    if not repos:
        raise ResourceNotFoundError("Repos are not provided")

    # Get local repositories
    local_repos = repos.get("local", {}).get(f"rhel-{os_version}")
    if not local_repos:
        raise ResourceNotFoundError("Local repositories are not provided")

    # Add local repositories
    for repo in local_repos:
        Package(ceph).add_repo(repo=repo)

    log.info("Added local RHEL repos successfully")
    return True

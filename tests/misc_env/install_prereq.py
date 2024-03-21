import base64
import os
import re
import time

from ceph.parallel import parallel
from ceph.utils import config_ntp
from ceph.waiter import WaitUntil
from cli.exceptions import ConfigError, UnexpectedStateError
from cli.utilities.configs import (
    get_configs,
    get_packages,
    get_registry_credentials,
    get_repos,
    get_subscription_credentials,
)
from cli.utilities.packages import Package, Pip, SubscriptionManager
from cli.utilities.utils import (
    create_json_config,
    enable_fips_mode,
    is_fips_mode_enabled,
    os_major_version,
    reboot_node,
    set_service_state,
    wait_for_node_to_ready,
)
from utility.log import Log

log = Log(__name__)

# Set home user home dir
CEPHUSER_HOME_DIR = "/home/cephuser"
ROOT_HOME_DIR = "/root"


def run(**kw):
    # DEBUG
    log.info("Configuring ceph cluster nodes")

    # Get cluster nodes
    ceph_nodes = kw.get("ceph_nodes")

    # Get cephci configs
    get_configs()

    # Get test configs
    config = kw.get("config")

    # Set test configs
    skip_subscription = config.get("skip_subscription", False)
    repo = config.get("add-repo", False)
    skip_enabling_rhel_rpms = config.get("skip_enabling_rhel_rpms", False)
    fips_mode = config.get("enable_fips_mode", False)
    cloud_type = config.get("cloud-type", "openstack")
    build = "ibm" if config.get("ibm_build") else "rh"

    # Start configuring nodes
    with parallel() as p:
        for ceph in ceph_nodes:
            p.spawn(
                install_prereq,
                ceph,
                skip_subscription,
                repo,
                skip_enabling_rhel_rpms,
                cloud_type,
                fips_mode,
                build,
            )

            # Explicitly wait for 30 sec to get configs effective
            time.sleep(30)

    return 0


def install_prereq(
    ceph,
    skip_subscription=False,
    repo=False,
    skip_enabling_rhel_rpms=False,
    cloud_type="openstack",
    fips_mode=False,
    build="rh",
):
    # Check for client node
    _is_client = len(ceph.role.role_list) == 1 and "client" in ceph.role.role_list

    # Get os major version
    os_version = os_major_version(ceph)

    # Wait for node to get ready
    wait_for_node_to_ready(ceph)

    # Get distro version
    distro_ver = ceph.distro_info.get("VERSION_ID")

    # DEBUG
    log.info(f"Distro Name: {ceph.distro_info.get('NAME')}")
    log.info(f"Distro ID: {ceph.distro_info.get('ID')}")
    log.info(f"Distro Version ID: {ceph.distro_info.get('VERSION_ID')}")

    # Remove apache-arrow.repo for baremetal
    ceph.remove_file("/etc/yum.repos.d/apache-arrow.repo", sudo=True)

    # Max SSH Sessions
    configure_ssh_sessions(ceph)

    if ceph.pkg_type == "deb":
        Package(ceph, manager="apt-get").install(
            ["wget", "git-core", "python-virtualenv", "lsb-release", "ntp"]
        )

    else:
        if distro_ver.startswith("7"):
            # Restart Network Manager service
            set_service_state(ceph, "NetworkManager.service", "restart")

        if not skip_subscription:
            setup_subscription_manager(ceph)

            status = subscription_manager_status(ceph)
            if status == "Unknown" or skip_enabling_rhel_rpms:
                log.info("Enabling local RHEL repositories")
                if not setup_local_repos(ceph):
                    raise ConfigError("Failed to enable local RHEL repositories")

            else:
                enable_rhel_rpms(ceph, distro_ver)

        if repo:
            # Add downstream repo
            Package(ceph).add_repo(repo)

            # Update repo metadata
            Package(ceph).update(metadata=True)

        # Upgrade installed packages
        Package(ceph).upgrade()

        # Get pakages to be installed
        version = f"rhel-{os_version}" if str(os_version) == "7" else None
        pkgs = get_packages(version)

        # Install packages
        Package(ceph).install(pkgs)

        # Restarting the node for qdisc filter to be loaded
        if not distro_ver.startswith("7"):
            reboot_node(ceph)

        # Install ansible builds
        if skip_enabling_rhel_rpms and skip_subscription and not _is_client:
            configure_ansible(ceph, distro_ver)

        # Set client packages
        if _is_client:
            # Install tool packages
            Package(ceph).install(["attr", "gcc"])

            # Install crefi python package
            Pip(ceph).install("crefi")

        # Clean repo cache
        Package(ceph).clean()

        # Configure NTP session
        config_ntp(ceph, cloud_type)

    # Login to container registry
    registry_login(ceph, distro_ver, build)

    # Update IP tables
    update_iptables(ceph)

    # Setup FIPS mode
    if fips_mode:
        setup_fips_mode(ceph)

    return


def configure_ansible(node, release):
    """Install ansible for downstream builds"""
    # Ansible is required for RHCS 4.x
    if release.startswith("8"):
        Package(node).install(
            "http://download-node-02.eng.bos.redhat.com/nightly/rhel-8/ANSIBLE/latest-ANSIBLE-2-RHEL-8/"
            "compose/Base/x86_64/os/Packages/ansible-2.9.27-1.el8ae.noarch.rpm"
        )

    elif release.startswith("7"):
        # Install ansible epel repo
        Package(node).install(
            "https://dl.fedoraproject.org/pub/epel/epel-release-latest-7.noarch.rpm",
            sudo=True,
        )

        # Install ansible 2.9
        Package(node).install("ansible-2.9.27-1.el7", sudo=True)


def configure_ssh_sessions(node):
    """Configure SSH session with maximum connections"""
    # Delete max session config
    node.exec_command(cmd="sed -i '/MaxSessions*/d' /etc/ssh/sshd_config", sudo=True)

    # Set maximum sessions to 150
    node.exec_command(
        cmd="echo 'MaxSessions 150' | tee -a /etc/ssh/sshd_config", sudo=True
    )

    # Restart SSHD service
    set_service_state(node, "sshd", "restart")


def setup_fips_mode(node):
    """Setup cluster node with FIPS mode on cluster node"""
    # Enable FIPS mode
    if not enable_fips_mode(node):
        raise ConfigError("Failed to enable FIPS mode")
    log.info("Enable FIPS mode config set successfully")

    # Restart node and wait
    reboot_node(node)

    # Check for FIPS mode setting
    if not is_fips_mode_enabled(node):
        raise ConfigError("FIPS mode not enabled after reboot")
    log.info("FIPS mode is enabled")


def setup_subscription_manager(node, timeout=300, interval=60):
    # Get subscription manager credentials
    creds = get_subscription_credentials()

    # Subscribe to server
    for w in WaitUntil(timeout=timeout, interval=interval):
        try:
            server = creds.get("serverurl")
            # Register node to subscription manager
            SubscriptionManager(node).register(
                username=creds.get("username"),
                password=creds.get("password"),
                serverurl=server,
                baseurl=creds.get("baseurl"),
                force=True,
            )
            log.info(f"Subscribed to {server} server successfully")
            return True
        except UnexpectedStateError:
            log.info(
                f"Unable to subscribe to {server} server. Retry after {interval} sec"
            )

    if w.expired:
        log.info(f"Failed to subscribe to {server} server after {timeout} sec")

    return False


def subscription_manager_status(ceph):
    expr = ".*Overall Status:(.*).*"
    status = SubscriptionManager(ceph).status()

    match = re.search(expr, status)
    if not match:
        raise UnexpectedStateError("Unexpected subscription manager status")

    return match.group(0)


def setup_local_repos(ceph):
    # Get distro version
    os_version = os_major_version(ceph)

    # Get local repositories
    repos = get_repos("production", f"rhel-{os_version}")

    # Add local repositories
    for repo in repos:
        Package(ceph).add_repo(repo=repo)

    log.info("Added local RHEL repos successfully")
    return True


def enable_rhel_rpms(ceph, release):
    """Setup cdn repositories for RHEL systems"""
    # Set RHEL release version
    SubscriptionManager(ceph).release(release)

    # Get os major version
    os_version = os_major_version(ceph)

    # Get live repositories
    repos = get_repos("released", f"rhel-{os_version}")

    # Enable live repos
    SubscriptionManager(ceph).repos.enable(repos)

    # Clean SM cache
    Package(ceph).clean()


def registry_login(node, release, build):
    """Login to container registry"""
    # Set container package based on RHEL
    pkg = "docker" if release.startswith("7") else "podman"

    # install container package
    Package(node).install(pkg)

    # Restart container service
    set_service_state(node, pkg, "restart")

    # Get registry credentails
    reg = get_registry_credentials(build)

    # Encrypt credentials
    b64_auth = base64.b64encode(f"{reg['username']}:{reg['password']}".encode("ascii"))

    # Set registry credentials
    auth = {"auths": {reg["registry"]: {"auth": b64_auth.decode("utf-8")}}}

    # Create cephuser configs
    cephuser_config_dir = os.path.join(CEPHUSER_HOME_DIR, "docker")
    cephuser_config_path = os.path.join(cephuser_config_dir, "config.json")

    # Create root user configs
    root_config_dir = os.path.join(ROOT_HOME_DIR, "docker")
    root_config_path = os.path.join(root_config_dir, "config.json")

    # Create docker config directory for users
    node.create_dirs(root_config_dir, sudo=True)
    node.create_dirs(cephuser_config_dir)

    # Create config files
    create_json_config(node, auth, cephuser_config_path)
    create_json_config(node, auth, root_config_path, sudo=True)


def update_iptables(node):
    """Update ip-tables rules"""
    drop_rules = ["INPUT -j REJECT --reject-with icmp-host-prohibited"]
    try:
        out, _ = node.exec_command(cmd="$(which iptables) --list-rules", sudo=True)
        for rule in drop_rules:
            if rule in out:
                node.exec_command(cmd=f"$(which iptables) -D {rule}", sudo=True)
    except Exception as err:
        log.error(f"iptables rpm do not exist... error : {err}")

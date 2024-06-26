from threading import Thread
from time import sleep

from nfs_operations import cleanup_cluster, perform_failover, setup_nfs_cluster

from cli.exceptions import ConfigError, OperationFailedError
from cli.utilities.utils import create_files
from cli.utilities.windows_utils import setup_windows_clients
from utility.log import Log

log = Log(__name__)


def modify_files(client, window_nfs_mount, file_count):
    """
    Modify files
    """
    for i in range(1, file_count):
        try:
            cmd = f"echo 'test'>{window_nfs_mount}\\win_file{i}"
            client.exec_command(cmd)
        except Exception:
            raise OperationFailedError(f"failed to modify file win_file{i}")


def run(ceph_cluster, **kw):
    """Verify HA with node reboot and modify file on windows client"""
    config = kw.get("config")
    # nfs cluster details
    nfs_nodes = ceph_cluster.get_nodes("nfs")
    no_servers = int(config.get("servers", "1"))
    if no_servers > len(nfs_nodes):
        raise ConfigError("The test requires more servers than available")
    servers = nfs_nodes[:no_servers]
    port = config.get("port", "2049")
    version = config.get("nfs_version", "3")
    fs_name = "cephfs"
    nfs_name = "cephfs-nfs"
    nfs_export = "/export"
    nfs_mount = "/mnt/nfs"
    window_nfs_mount = "Z:"
    fs = "cephfs"
    nfs_server_name = [nfs_node.hostname for nfs_node in servers]
    ha = bool(config.get("ha", False))
    vip = config.get("vip", None)
    port = "12049"

    # Linux clients
    linux_clients = ceph_cluster.get_nodes("client")
    no_linux_clients = int(config.get("linux_clients", "1"))
    linux_clients = linux_clients[:no_linux_clients]
    if no_linux_clients > len(linux_clients):
        raise ConfigError("The test requires more linux clients than available")

    # Windows clients
    for windows_client_obj in setup_windows_clients(config.get("windows_clients")):
        ceph_cluster.node_list.append(windows_client_obj)
    windows_clients = ceph_cluster.get_nodes("windows_client")

    try:
        # Setup nfs cluster
        setup_nfs_cluster(
            linux_clients,
            nfs_server_name,
            port,
            version,
            nfs_name,
            nfs_mount,
            fs_name,
            nfs_export,
            fs,
            ha,
            vip,
            ceph_cluster=ceph_cluster,
        )

        # Fetch the VIP
        if "/" in vip:
            vip = vip.split("/")[0]

        # Mount NFS-Ganesha V3 to window
        cmd = f"mount {vip}:/export_0 {window_nfs_mount}"
        windows_clients[0].exec_command(cmd=cmd)
        sleep(5)

        operations = []

        # Run IO's from windows client
        create_files(windows_clients[0], window_nfs_mount, 10, True)

        failover_node = nfs_nodes[0]

        # Perform node reboot operation
        th = Thread(
            target=perform_failover,
            args=(nfs_nodes, failover_node, vip),
        )
        operations.append(th)

        # Modify the content of file
        th = Thread(
            target=modify_files,
            args=(windows_clients[0], window_nfs_mount, 10),
        )
        operations.append(th)

        # Start the operations
        for op in operations:
            op.start()
            sleep(1)

        # Wait for the ops to complete
        for op in operations:
            op.join()

    except Exception as e:
        log.error(
            f"Failed to validate export delete with failover on a ha cluster: {e}"
        )
        # Cleanup
        cleanup_cluster(linux_clients, nfs_mount, nfs_name, nfs_export)
        return 1
    finally:
        # Cleanup
        log.info("Cleanup")
        cleanup_cluster(linux_clients, nfs_mount, nfs_name, nfs_export)
    return 0

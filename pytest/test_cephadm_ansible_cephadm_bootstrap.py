import os

from cli.cephadm.ansible import Ansible
from cli.cephadm.cephadm import CephAdm
from cli.utilities.packages import Package, Rpm, RpmError
from cli.utilities.utils import get_node_ip
from utility.install_prereq import (
    ConfigureCephadmAnsibleInventory,
    EnableToolsRepositories,
)
from utility.log import Log

CEPHADM_ANSIBLE_PATH = "/usr/share/cephadm-ansible"
CEPHADM_PREFLIGHT_PLAYBOOK = "cephadm-preflight.yml"
CEPHADM_PREFLIGHT_VARS = {"ceph_origin": "rhcs"}

log = Log()


def put_cephadm_ansible_playbook(node, playbook):
    """Put playbook to cephadm ansible location.
    Args:
        playbook (str): Playbook need to be copied to cephadm ansible path
    """
    dst = os.path.join(CEPHADM_ANSIBLE_PATH, os.path.basename(playbook))

    node.upload_file(sudo=True, src=playbook, dst=dst)
    log.info(f"Uploaded playbook '{playbook}' to '{dst}' on node.")


def test_cephadm_ansible_cephadm_bootstrap(cluster, cluster_dict):
    """Test to validate `cephadm_bootstrap` cephadm ansible module"""
    ceph_cluster = cluster_dict.get(cluster)

    nodes = ceph_cluster.get_nodes()
    installer = ceph_cluster.get_ceph_object("installer")

    playbook, extra_vars, extra_args = "playbooks/bootstrap-cluster.yml", {}, {}
    mon_node = "node1"

    try:
        Rpm(installer).query("cephadm-ansible")
    except RpmError:
        ceph_cluster.setup_ssh_keys()
        EnableToolsRepositories().run(installer)

        Package(installer).install("cephadm-ansible")

        ConfigureCephadmAnsibleInventory().run(nodes)
        Ansible(installer).run_playbook(
            playbook=CEPHADM_PREFLIGHT_PLAYBOOK,
            extra_vars=CEPHADM_PREFLIGHT_VARS,
        )

    extra_vars["mon_ip"] = get_node_ip(nodes, mon_node)
    put_cephadm_ansible_playbook(installer, playbook)
    Ansible(installer).run_playbook(
        playbook=os.path.basename(playbook),
        extra_vars=extra_vars,
        extra_args=extra_args,
    )

    assert CephAdm(installer).ceph.status()

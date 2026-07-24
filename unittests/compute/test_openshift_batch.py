from compute.openshift import (
    build_virtualmachine_cr,
    non_root_datavolume_names,
    process_ocpvirt_custom_config,
)


def test_process_ocpvirt_custom_config_defaults():
    cfg = process_ocpvirt_custom_config(None)
    assert cfg == {"pvc_batch_size": 3, "vm_batch_size": 1}


def test_process_ocpvirt_custom_config_overrides():
    cfg = process_ocpvirt_custom_config(
        ["pvc_batch_size=5", "vm_batch_size=2", "ibm-build=True"]
    )
    assert cfg == {"pvc_batch_size": 5, "vm_batch_size": 2}


def test_process_ocpvirt_custom_config_invalid_values():
    cfg = process_ocpvirt_custom_config(["pvc_batch_size=abc", "vm_batch_size=0"])
    assert cfg == {"pvc_batch_size": 3, "vm_batch_size": 1}


def test_non_root_datavolume_names():
    names = non_root_datavolume_names("ceph-foo-bar", 2)
    assert names == ["ceph-foo-bar-vol-0", "ceph-foo-bar-vol-1"]


def test_build_virtualmachine_cr_precreated_volumes():
    vm = build_virtualmachine_cr(
        node_name="ceph-test-node",
        namespace="test-ns",
        image_name="https://example.com/disk.qcow2",
        storage_class="nfs",
        network="bridge-504",
        cpu="4",
        memory="8Gi",
        root_disk_size="80Gi",
        precreated_volume_names=["ceph-test-node-vol-0", "ceph-test-node-vol-1"],
    )
    templates = vm["spec"]["dataVolumeTemplates"]
    assert len(templates) == 1
    assert templates[0]["metadata"]["name"] == "ceph-test-node-root"

    volumes = vm["spec"]["template"]["spec"]["volumes"]
    dv_names = [vol["dataVolume"]["name"] for vol in volumes if "dataVolume" in vol]
    assert "ceph-test-node-vol-0" in dv_names
    assert "ceph-test-node-vol-1" in dv_names

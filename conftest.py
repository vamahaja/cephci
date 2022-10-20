import pytest
import pickle
import os
import logging

from utility.utils import create_run_dir, generate_unique_id
from utility.log import Log

CLUSTER_DICT = None

log = Log()


def pytest_addoption(parser):
    parser.addoption(
        "--reuse", action="store", help="Use the stored vm state for rerun"
    )
    parser.addoption(
        "--log-dir", action="store", help="Set log directory"
    )


def _set_logging(config, run_id):
    log_directory = config.getoption("--log-dir")
    run_dir = create_run_dir(run_id, log_directory)

    startup_log = os.path.join(run_dir, "startup.log")
    handler = logging.FileHandler(startup_log)
    log.logger.addHandler(handler)

    log_level = config.getoption("--log-level")
    if log_level:
        log.logger.setLevel(log_level.upper())

    log.info(f"Startup log location: {startup_log}")


def _get_cluster_dict(config):
    global CLUSTER_DICT
    if CLUSTER_DICT:
        return

    reuse = config.getoption("--reuse")
    with open(reuse, "rb") as _reuse:
        CLUSTER_DICT = pickle.load(_reuse)

    for _, cluster in CLUSTER_DICT.items():
        for node in cluster:
            node.reconnect()


@pytest.fixture(scope="session")
def cluster_dict():
    global CLUSTER_DICT

    return CLUSTER_DICT


def pytest_generate_tests(metafunc):
    run_id = generate_unique_id(length=6)

    _set_logging(metafunc.config, run_id)
    _get_cluster_dict(metafunc.config)

    metafunc.parametrize("cluster", CLUSTER_DICT.keys())

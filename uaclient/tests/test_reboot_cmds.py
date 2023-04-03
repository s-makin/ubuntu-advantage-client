import logging

import mock
import pytest

from lib.reboot_cmds import fix_pro_pkg_holds, main, process_reboot_operations
from uaclient import messages
from uaclient.exceptions import ProcessExecutionError
from uaclient.files.notices import Notice

M_FIPS_PATH = "uaclient.entitlements.fips.FIPSEntitlement."


class TestMain:
    @pytest.mark.parametrize("caplog_text", [logging.WARNING], indirect=True)
    def test_retries_on_lock_file(self, FakeConfig, caplog_text):
        cfg = FakeConfig.for_attached_machine()
        with pytest.raises(SystemExit) as excinfo:
            with mock.patch(
                "uaclient.config.UAConfig.check_lock_info"
            ) as m_check_lock:
                m_check_lock.return_value = (123, "pro auto-attach")
                with mock.patch("time.sleep") as m_sleep:
                    main(cfg=cfg)
        assert [
            mock.call(1),
            mock.call(1),
            mock.call(1),
            mock.call(1),
            mock.call(1),
            mock.call(1),
        ] == m_sleep.call_args_list
        assert 1 == excinfo.value.code
        assert (
            "Lock not released. Unable to perform: ua-reboot-cmds"
        ) in caplog_text()

    @pytest.mark.parametrize("caplog_text", [logging.DEBUG], indirect=True)
    @mock.patch(
        "uaclient.files.state_files.reboot_cmd_marker_file",
        new_callable=mock.PropertyMock,
    )
    def test_main_unattached_removes_marker(
        self,
        m_reboot_cmd_marker_file,
        FakeConfig,
        caplog_text,
    ):
        cfg = FakeConfig()
        m_reboot_cmd_marker_file.is_present = True
        main(cfg=cfg)
        assert [mock.call()] == m_reboot_cmd_marker_file.delete.call_args_list
        assert "Skipping reboot_cmds. Machine is unattached" in caplog_text()

    @mock.patch(
        "uaclient.files.state_files.reboot_cmd_marker_file",
        new_callable=mock.PropertyMock,
    )
    @mock.patch("lib.reboot_cmds.fix_pro_pkg_holds")
    def test_main_noops_when_not_attached(
        self, m_fix_pro_pkg_holds, m_reboot_cmd_marker_file, FakeConfig
    ):
        m_reboot_cmd_marker_file.is_present = True
        cfg = FakeConfig()
        main(cfg=cfg)
        assert [] == m_fix_pro_pkg_holds.call_args_list

    @mock.patch(
        "uaclient.files.state_files.reboot_cmd_marker_file",
        new_callable=mock.PropertyMock,
    )
    @mock.patch("lib.reboot_cmds.fix_pro_pkg_holds")
    def test_main_noops_when_no_marker(
        self,
        m_fix_pro_pkg_holds,
        m_reboot_cmd_marker_file,
        FakeConfig,
    ):
        m_reboot_cmd_marker_file.is_present = False
        cfg = FakeConfig.for_attached_machine()
        main(cfg=cfg)
        assert [] == m_fix_pro_pkg_holds.call_args_list


M_REPO_PATH = "uaclient.entitlements"


class TestFixProPkgHolds:
    @pytest.mark.parametrize("caplog_text", [logging.WARN], indirect=True)
    @pytest.mark.parametrize("fips_status", ("enabled", "disabled"))
    @mock.patch("sys.exit")
    @mock.patch(M_FIPS_PATH + "install_packages")
    @mock.patch(M_FIPS_PATH + "setup_apt_config")
    @mock.patch("uaclient.files.notices.NoticesManager.remove")
    def test_calls_setup_apt_config_and_install_packages_when_enabled(
        self,
        m_remove_notice,
        setup_apt_config,
        install_packages,
        exit,
        fips_status,
        FakeConfig,
        caplog_text,
    ):
        cfg = FakeConfig()
        fake_status_cache = {
            "services": [{"name": "fips", "status": fips_status}]
        }
        cfg.write_cache("status-cache", fake_status_cache)

        fix_pro_pkg_holds(cfg=cfg)
        if fips_status == "enabled":
            assert [mock.call()] == setup_apt_config.call_args_list
            assert [
                mock.call(cleanup_on_failure=False)
            ] == install_packages.call_args_list
        else:
            assert 0 == setup_apt_config.call_count
            assert 0 == install_packages.call_count
            assert 0 == len(m_remove_notice.call_args_list)
        assert 0 == exit.call_count


class TestProcessRebootOperations:
    @pytest.mark.parametrize("caplog_text", [logging.ERROR], indirect=True)
    @mock.patch("uaclient.config.UAConfig.delete_cache_key")
    @mock.patch("uaclient.config.UAConfig.check_lock_info")
    @mock.patch("uaclient.files.notices.NoticesManager.add")
    @mock.patch("lib.reboot_cmds.fix_pro_pkg_holds")
    def test_process_reboot_operations_create_notice_when_it_fails(
        self,
        m_fix_pro_pkg_holds,
        m_add_notice,
        m_check_lock_info,
        _m_delete_cache_key,
        FakeConfig,
        caplog_text,
    ):
        m_check_lock_info.return_value = (0, 0)
        m_fix_pro_pkg_holds.side_effect = ProcessExecutionError("error")

        cfg = FakeConfig.for_attached_machine()
        with mock.patch("os.path.exists", return_value=True):
            with mock.patch("uaclient.config.UAConfig.write_cache"):
                process_reboot_operations(cfg=cfg)

        expected_calls = [
            mock.call(
                Notice.REBOOT_SCRIPT_FAILED,
                messages.REBOOT_SCRIPT_FAILED,
            ),
        ]

        assert expected_calls == m_add_notice.call_args_list

        expected_msgs = [
            "Failed running commands on reboot.",
            "Invalid command specified 'error'.",
        ]
        assert all(
            expected_msg in caplog_text() for expected_msg in expected_msgs
        )

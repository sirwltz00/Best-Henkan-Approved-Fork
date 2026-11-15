import unittest

from system_requirements import collect_system_info, evaluate_system, overall_status


class SystemRequirementsTests(unittest.TestCase):
    def sample(self, **changes):
        info = {
            'os_name': 'Windows 11 (build 22631)',
            'is_windows': True,
            'is_supported_windows': True,
            'architecture': 'AMD64',
            'process_bits': 64,
            'is_64bit': True,
            'processor': 'Example CPU',
            'logical_cores': 8,
            'ram_total_bytes': 16 * 1024 ** 3,
            'ram_available_bytes': 8 * 1024 ** 3,
            'disk_free_bytes': 40 * 1024 ** 3,
            'disk_path': 'C:\\',
            'screen_width': 1920,
            'screen_height': 1080,
            'worker_path': 'C:\\Henkan\\HenkanWorker.exe',
            'worker_exists': True,
            'frozen': True,
        }
        info.update(changes)
        return info

    def test_collector_returns_read_only_diagnostic_fields(self):
        info = collect_system_info(app_dir='.', screen_size=(1366, 768), frozen=False)
        self.assertIn('is_windows', info)
        self.assertIn('ram_total_bytes', info)
        self.assertIn('disk_free_bytes', info)
        self.assertEqual((info['screen_width'], info['screen_height']), (1366, 768))
        self.assertTrue(info['worker_path'].endswith('henkan_worker.py'))

    def test_recommended_pc_passes(self):
        rows = evaluate_system(self.sample())
        self.assertTrue(all(row['status'] in ('good', 'info') for row in rows))
        self.assertEqual(overall_status(rows)[0], 'good')

    def test_unsupported_os_and_architecture_are_flagged(self):
        rows = evaluate_system(self.sample(is_windows=False, is_supported_windows=False, is_64bit=False, process_bits=32,
                                           os_name='Linux', architecture='x86'))
        platform_row = next(row for row in rows if row['key'] == 'platform')
        self.assertEqual(platform_row['status'], 'bad')
        self.assertEqual(overall_status(rows)[0], 'bad')

    def test_minimum_working_pc_is_not_flagged_as_below_minimum(self):
        rows = evaluate_system(self.sample(logical_cores=2, ram_total_bytes=4 * 1024 ** 3,
                                           disk_free_bytes=2 * 1024 ** 3,
                                           screen_width=820, screen_height=640))
        self.assertFalse(any(row['status'] in ('warn', 'bad') for row in rows))
        self.assertEqual(overall_status(rows)[0], 'good')

    def test_low_ram_and_disk_warn_without_blocking(self):
        rows = evaluate_system(self.sample(ram_total_bytes=2 * 1024 ** 3,
                                           disk_free_bytes=1 * 1024 ** 3))
        self.assertEqual(next(row for row in rows if row['key'] == 'ram')['status'], 'warn')
        self.assertEqual(next(row for row in rows if row['key'] == 'disk')['status'], 'warn')
        self.assertEqual(overall_status(rows)[0], 'warn')

    def test_missing_worker_is_compatibility_error(self):
        rows = evaluate_system(self.sample(worker_exists=False))
        self.assertEqual(next(row for row in rows if row['key'] == 'worker')['status'], 'bad')

    def test_small_display_is_flagged(self):
        rows = evaluate_system(self.sample(screen_width=800, screen_height=600))
        self.assertEqual(next(row for row in rows if row['key'] == 'display')['status'], 'warn')


if __name__ == '__main__':
    unittest.main()

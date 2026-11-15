"""Tests for the subprocess dispatcher used by frozen Windows builds."""
import sys
import types

import henkan_worker


def test_worker_dispatches_cli_and_sets_argv(monkeypatch):
    fake = types.ModuleType('henkan')
    seen = {}

    def run():
        seen['argv'] = list(sys.argv)
        return 7

    fake.main = run
    monkeypatch.setitem(sys.modules, 'henkan', fake)

    assert henkan_worker.main(['--cli', 'convert', 'bank', 'out']) == 7
    assert seen['argv'] == ['henkan.py', 'convert', 'bank', 'out']


def test_worker_dispatches_audio_qa(monkeypatch):
    package = types.ModuleType('tools')
    package.__path__ = []
    qa = types.ModuleType('tools.qa')
    seen = {}

    def run(args):
        seen['args'] = args
        return 0

    qa.main = run
    monkeypatch.setitem(sys.modules, 'tools', package)
    monkeypatch.setitem(sys.modules, 'tools.qa', qa)

    assert henkan_worker.main(['--qa', 'converted-folder']) == 0
    assert seen['args'] == ['converted-folder']


def test_worker_rejects_unknown_mode():
    assert henkan_worker.main(['--not-a-mode']) == 2

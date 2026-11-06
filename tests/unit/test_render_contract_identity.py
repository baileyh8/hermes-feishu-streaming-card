"""Package build metadata is not the rendered-hook compatibility contract."""
from pathlib import Path
import ast

from hermes_feishu_card import admission_topology_0226 as topology
from hermes_feishu_card.install import patcher


def test_contract_identity_is_explicit_and_matches_pinned_digests(monkeypatch):
    import importlib
    import hermes_feishu_card

    monkeypatch.setattr(hermes_feishu_card, '__version__', '5.0.17+another.build')
    importlib.reload(topology)
    assert topology._HFC_VERSION == patcher.RENDER_CONTRACT_VERSION
    assert topology._HFC_VERSION == topology.RENDER_VERSION_0230
    tree = ast.parse(Path(topology.__file__).read_text())
    imports = [node for node in tree.body if isinstance(node, ast.ImportFrom)]
    assert any(node.module == 'install.patcher' and any(
        alias.name == 'RENDER_CONTRACT_VERSION' and alias.asname == '_HFC_VERSION'
        for alias in node.names) for node in imports)
    assert not any(any(alias.name == '__version__' for alias in node.names)
                   for node in imports)

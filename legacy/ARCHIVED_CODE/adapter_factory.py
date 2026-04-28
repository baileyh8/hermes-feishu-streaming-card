
# adapter/factory.py
from .streaming_adapter import StreamingAdapter
from .legacy_adapter import LegacyAdapter
from .sidecar_adapter import SidecarAdapter
from .dual_adapter import DualAdapter

class AdapterFactory:
    @staticmethod
    def create_adapter(config: dict) -> StreamingAdapter:
        mode = config.get('feishu_streaming_card', {}).get('mode', 'legacy')
        if mode == 'sidecar':
            return SidecarAdapter(config)
        elif mode == 'dual':
            return DualAdapter(config)
        else:
            return LegacyAdapter(config)

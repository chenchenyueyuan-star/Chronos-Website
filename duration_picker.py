"""Accessible scroll wheel for choosing a whole number of minutes."""
from pathlib import Path
from streamlit.components.v1 import declare_component

_component = declare_component('chronos_minute_wheel', path=str(Path(__file__).parent / 'components' / 'minute_wheel'))


def minute_wheel(value=10, key='duration_wheel'):
    selected = _component(value=value, default=value, key=key)
    if not isinstance(selected, int) or isinstance(selected, bool) or not 1 <= selected <= 30:
        return value
    return selected

"""Small earthquake notifier: watches public seismic feeds and pushes alerts."""

from importlib.metadata import version

__version__ = version("eq-notifier")

USER_AGENT = f"eq-notifier/{__version__} (https://github.com/mr-grj/eq_notifier)"

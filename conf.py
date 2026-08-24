# See the Documenteer docs for how to customize conf.py:
# https://documenteer.lsst.io/technotes/

from documenteer.conf.technote import *  # noqa F401 F403

html_static_path = ["_static"]
html_css_files = ["custom.css"]

# The mppdb service and the RSP token page require Rubin SSO, so an
# unauthenticated linkcheck gets 401 on URLs that are in fact correct.
linkcheck_ignore = [
    r"https://usdf-rsp-dev\.slac\.stanford\.edu/.*",
]

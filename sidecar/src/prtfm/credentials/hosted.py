"""Read the installed host key without exporting it to child environments."""
from prtfm.credentials.provider import configure
from prtfm.credentials.windows import WindowsCredentials


def configure_hosted_key(channel: str) -> None:
    configure()  # fail closed, including when Credential Manager raises
    configure(WindowsCredentials().read_for_provider(channel))

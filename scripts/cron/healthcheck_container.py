import sys
import urllib.error
import urllib.request


def main() -> int:
    try:
        with urllib.request.urlopen("http://127.0.0.1:8080/healthz", timeout=10) as response:
            if response.status != 200:
                return 1
    except (urllib.error.URLError, TimeoutError):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

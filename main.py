from deathtg.dependency_bootstrap import ensure_core_dependencies

ensure_core_dependencies()

from deathtg.main import run


if __name__ == "__main__":
    run()

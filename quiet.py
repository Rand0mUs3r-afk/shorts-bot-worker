"""Expose only a truthful final status; suppress Python and native output."""
import os
import sys


def main():
    output = os.dup(1)
    success = False
    try:
        os.umask(0o077)
        with open(os.devnull, 'w') as sink:
            sys.stdout.flush()
            sys.stderr.flush()
            os.dup2(sink.fileno(), 1)
            os.dup2(sink.fileno(), 2)
            for name in ('GITHUB_STEP_SUMMARY', 'GITHUB_OUTPUT', 'GITHUB_ENV', 'GITHUB_PATH'):
                os.environ[name] = os.devnull
            import worker
            worker.main()
            success = True
    except BaseException:
        pass
    finally:
        os.write(output, b'Success\n' if success else b'Failed\n')
        os.close(output)
    return 0 if success else 1


if __name__ == '__main__':
    sys.exit(main())

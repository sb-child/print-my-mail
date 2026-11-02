from .client import DEFAULT_TARGET, PrintMyMailClient

__all__ = ["DEFAULT_TARGET", "PrintMyMailClient", "main"]


def main() -> None:
    print("Hello from print-my-mail!")
    cli = PrintMyMailClient(DEFAULT_TARGET)
    

import argparse
import sys

from cryptography.fernet import InvalidToken

from kelso.lib.crypto import FernetCryptoEngine
from kelso.lib.kelso import KelsoCtx


def register(subparsers) -> None:
  parser = subparsers.add_parser(
    "decrypt",
    help="Decrypt a value from stdin using kelso's master key",
  )
  # Reads nothing from kelsodb and writes nothing anywhere.
  parser.set_defaults(func=run)


def run(args: argparse.Namespace, ctx: KelsoCtx) -> None:
  if not ctx.config.master_key:
    raise ValueError(
      f"No recovery phrase in {ctx.config.master_keyfile}. Make one with "
      f"`kelso system rekey`."
    )

  blob = sys.stdin.read().strip()
  if not blob:
    raise ValueError("Nothing on stdin to decrypt")

  try:
    # Fernet is authenticated: a wrong key raises, never returns garbage.
    plaintext = FernetCryptoEngine(
      ctx.config.master_key, ctx.config.retired_master_keys
    ).decrypt(blob)
  except InvalidToken:
    raise ValueError(
      "Could not decrypt that value. It is not a kelso-encrypted blob, or it "
      "was encrypted with a different master key"
    ) from None

  print(plaintext)

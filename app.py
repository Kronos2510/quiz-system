"""Vercel entrypoint.

Vercel's Python runtime looks for a FastAPI instance named ``app`` in a
supported entry file. All application code lives in the ``quizapp`` package.
"""

from quizapp.main import app  # noqa: F401

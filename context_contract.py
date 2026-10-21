"""Versioned contracts for stored context and the worker-facing view."""
import json
from pathlib import Path
from memory_store import validate
SCHEMA=json.loads((Path(__file__).resolve().parent/'schemas/context.schema.json').read_text())
def check(name,value):validate(value,SCHEMA['$defs'][name],root_schema=SCHEMA)

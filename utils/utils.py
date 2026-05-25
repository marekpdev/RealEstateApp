import json
import os
from typing import TypeVar, Any
from pydantic import BaseModel

# Create a Type Generic variable bound to Pydantic's BaseModel
T = TypeVar("T", bound=BaseModel)

def print_model(model_instance: T, title: str = "MODEL") -> None:
    """
    Utility helper that converts any nested Pydantic model into a
    pretty-printed, indented JSON blueprint string for console debugging.
    """
    # 1. Edge guard clause to ensure we are passing a genuine Pydantic model
    if not isinstance(model_instance, BaseModel):
        print(f"❌ [Error] Object passed to print_pydantic_schematic is not a Pydantic model. Type: {type(model_instance)}")
        return

    try:
        # 2. Extract model class name dynamically
        model_class_name = type(model_instance).__name__

        # 3. Serialize the model into a raw JSON string using Pydantic's internal framework
        raw_json_string = model_instance.model_dump_json()

        # 4. Standardize the indentation layout using Python's core json engine
        pretty_json = json.dumps(json.loads(raw_json_string), indent=4)

        # 5. Format and write the schematic output directly to the system console
        print(f"\n================ 🟢 {title.upper()} ({model_class_name}) ================")
        print(pretty_json)
        print("================================================================")

    except Exception as e:
        print(f"❌ [Error] Failed to print schematic framework for model instance. Exception: {e}")

def get_env_bool(key: str, default: bool = False) -> bool:
    """Safely extracts an environment variable and parses it directly into a Boolean."""
    fallback_str = "false" if default else "true"
    return os.getenv(key, fallback_str).lower() in ("true", "1", "yes")
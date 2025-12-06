import ollama
import json
import typing_extensions as typing
from typing import Optional, List, Dict, Any

class RecipeIngredient(typing.TypedDict):
    ingredient_name: str
    quantity: float
    unit: str
    cost: float
    note: Optional[str]

class RecipeMetadata(typing.TypedDict):
    dish_name: str
    business_name: str
    servings: int
    shelf_life_days: int
    menu_price: float
    cost_percent: float
    final_price: float
    prep_time_minutes: int
    storage_temp_fahrenheit: int
    serving_cost: float

class Recipe(typing.TypedDict):
    metadata: RecipeMetadata
    ingredients: List[RecipeIngredient]

def parse_recipe_with_llm(text: str) -> Optional[Recipe]:
    """
    Parse recipe text using local Ollama LLM (llama3.2:3b) to extract structured data.
    """
    try:
        prompt = f"""
        Extract the recipe information from the following text into a structured JSON format.
        
        The text contains a recipe with metadata (dish name, business name, servings, etc.) and a list of ingredients.
        For each ingredient, extract the name, quantity, unit, cost, and any notes.
        Note: "each" is a valid unit. If the unit cannot be determined, but "each" is present after the ingredient name, use "each" as the unit.
        
        If a value is not present, use null (None in Python) or 0 for numbers.
        
        Text:
        {text}
        
        Return ONLY valid JSON matching this structure:
        {{
            "metadata": {{
                "dish_name": "string",
                "business_name": "string",
                "servings": 0,
                "shelf_life_days": 0,
                "menu_price": 0.0,
                "cost_percent": 0.0,
                "final_price": 0.0,
                "prep_time_minutes": 0,
                "storage_temp_fahrenheit": 0,
                "serving_cost": 0.0
            }},
            "ingredients": [
                {{
                    "ingredient_name": "string",
                    "quantity": 0.0,
                    "unit": "string",
                    "cost": 0.0,
                    "note": "string"
                }}
            ]
        }}
        """
        
        response = ollama.chat(model='llama3.2:3b', messages=[
            {
                'role': 'user',
                'content': prompt,
            },
        ], format='json')
        
        content = response['message']['content']
        if content:
            return json.loads(content)
            
    except Exception as e:
        print(f"Error parsing recipe with LLM: {e}")
        return None

- ingredient_name (base name only)
- primary_variety (main cultivar/type) or empty
- secondary_variety (processing/quality) or empty
- source_ingredient_name (verbatim from source)
- quantity (number) and unit (string) — these MUST NOT be empty. (VERY VERY VERY IMPORTANT)
• quantity: a positive number (float). Do not return null.
• unit: one of these canonical, lowercase tokens only (choose the most appropriate):
["each","portion","pinch","tsp","tbsp","cup","pt","qt","gal","ml","l","g","kg","oz","lb"]
If the text implies a count (e.g., eggs, buns), use "each". If the text gives a weight/volume, prefer the corresponding canonical unit. Do not invent unusual units.
• Normalize synonyms: teaspoons→"tsp", tablespoons→"tbsp", pounds→"lb", ounces→"oz", liters→"l", milliliters→"ml".
• Never leave quantity or unit blank; if clearly unspecified but implied by context, infer the most sensible quantity+unit from the line (e.g., counts as 1.0 each). Do not output nulls.


CRITICAL: For each ingredient, parse into FOUR distinct fields:
- ingredient_name: Base ingredient only (e.g., "Apple", "Black Pepper", "Matcha")
- primary_variety: Primary variety/cultivar (e.g., "Granny Smith", "Roma", "") - Leave empty if no specific variety
- secondary_variety: Processing/quality attributes (e.g., "Organic", "Ground", "Ceremonial Grade") - Leave empty if none
- source_ingredient_name: Exact text as written in document
PARSING HIERARCHY:
L0 (ingredient_name): The base ingredient category/type - Apple, Black Pepper, Matcha, Cashews
L1 (primary_variety): The specific variety, cultivar, or type within that ingredient - Granny Smith, Roma, (empty), Raw
L2 (secondary_variety): Processing method, quality grade, or preparation state - Organic, Ground, Ceremonial Grade, (empty)
✅ CORRECT Examples:
- "Apple, Granny Smith, Organic" → ingredient_name="Apple", primary_variety="Granny Smith", secondary_variety="Organic", source_ingredient_name="Apple, Granny Smith, Organic"
- "Black Pepper, ground" → ingredient_name="Black Pepper", primary_variety="", secondary_variety="Ground", source_ingredient_name="Black Pepper, ground"
- "Matcha, Ceremonial Grade" → ingredient_name="Matcha", primary_variety="", secondary_variety="Ceremonial Grade", source_ingredient_name="Matcha, Ceremonial Grade"

❌ WRONG - DO NOT DO THIS:
- "Hemp Seeds 1lb" → ingredient_name="Hemp Seeds", primary_variety="1lb" ← WRONG! Units go in quantity/unit fields
- "Dragon Sauce (Subrecipe)" → ingredient_name="Dragon Sauce", primary_variety="Subrecipe" ← WRONG! Use is_subrecipe field
- "Black Pepper, ground 1oz" → ingredient_name="Black Pepper", secondary_variety="ground 1oz" ← WRONG! No units in variety
IMPORTANT:
- primary_variety should ONLY contain variety/cultivar info like "Granny Smith", "Roma", "Curly"
- secondary_variety should ONLY contain processing/quality info like "Fresh", "Organic", "Diced", "Ground"
- quantity/unit are REQUIRED per ingredient and must be present and canonicalized as above (never empty)

Additionally, for each ingredient output these fields:
- brand: only if explicitly present in the text; otherwise null
- category: REQUIRED string and must be one of EXACTLY these labels:
"Produce", "Meat & Poultry", "Seafood", "Dairy & Eggs", "Dry Goods & Pantry",
"Frozen Foods", "Bakery & Breads", "Beverages", "Oils, Fats & Sauces",
"Paper Goods & Packaging", "Cleaning & Chemicals", "Disposables & Smallwares", "Other"
If unsure, set category to "Other".
- perishable: REQUIRED boolean (true/false). If not clear, default to true.


create table public.ingredients (
  ingredient_id uuid not null default extensions.uuid_generate_v4 (),
  ingredient_name character varying(255) not null,
  category text null,
  base_unit text null,
  perishable boolean null,
  created_at timestamp with time zone null default now(),
  updated_at timestamp with time zone null default now(),
  subcategory text null,
  primary_variety text null,
  brand character varying(100) null,
  attributes jsonb null default '{}'::jsonb,
  source_ingredient_name character varying(255) null,
  is_base_ingredient boolean null default true,
  base_ingredient_id uuid null,
  secondary_variety text null,
  base_category text null,
  constraint ingredients_pkey primary key (ingredient_id),
  constraint ingredients_base_ingredient_id_fkey foreign KEY (base_ingredient_id) references ingredients (ingredient_id)
) TABLESPACE pg_default;
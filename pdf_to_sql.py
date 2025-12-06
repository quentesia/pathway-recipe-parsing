"""
PDF Recipe Parser to SQL Database
Pure Python implementation using pdfplumber for table extraction
No Java dependencies required

DATA STORAGE:
- Parsed recipe data is inserted into the PostgreSQL 'recipes' table
- Schema defined in recipe_schema.sql
- Each ingredient becomes one row in the recipes table
- Foreign keys link to 'ingredients' and 'businesses' tables
"""

import pdfplumber
import pandas as pd
import psycopg2
from psycopg2.extras import execute_values
import uuid
from decimal import Decimal
import re
from typing import Dict, List, Tuple, Optional
import zipfile
import tempfile
import os
from contextlib import contextmanager
from llm_parser import parse_recipe_with_llm


# ============================================================================
# Database Connection
# ============================================================================

@contextmanager
def get_db_connection(connection_string: str):
    """
    Context manager for database connection
    Automatically commits on success, rolls back on error
    """
    conn = psycopg2.connect(connection_string)
    cursor = conn.cursor()
    try:
        yield cursor
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


# ============================================================================
# PDF Text Extraction
# ============================================================================

def extract_text_from_pdf(pdf_path: str) -> str:
    """Extract all text from first page of PDF"""
    with pdfplumber.open(pdf_path) as pdf:
        text = pdf.pages[0].extract_text()
        return text


def extract_tables_from_pdf(pdf_path: str) -> List[List[List[str]]]:
    """
    Extract all tables from first page of PDF using pdfplumber
    Returns list of tables, where each table is a list of rows
    """
    with pdfplumber.open(pdf_path) as pdf:
        page = pdf.pages[0]
        tables = page.extract_tables()

        # Debug output
        print(f"  DEBUG: Found {len(tables) if tables else 0} tables using extract_tables()")
        if tables:
            for i, table in enumerate(tables):
                print(f"  DEBUG: Table {i+1} has {len(table)} rows, {len(table[0]) if table else 0} columns")
                if table and len(table) > 0:
                    print(f"  DEBUG: Headers: {table[0]}")

        return tables if tables else []


def parse_table_from_text(text: str) -> List[List[str]]:
    """
    Fallback: Parse table structure directly from text when extract_tables() fails
    Looks for lines matching ingredient pattern
    """
    lines = text.split('\n')
    table = []

    # Find the header line
    header_found = False
    for i, line in enumerate(lines):
        if 'ITEMS' in line and 'CU COST' in line and 'SIZE YIELD' in line and 'RU COST' in line:
            # Split header by multiple spaces
            header = ['ITEMS', 'CU COST', 'SIZE YIELD %', 'RU COST', 'NOTES']
            table.append(header)
            header_found = True
            print(f"  DEBUG: Found header at line {i}: {line}")

            # Parse subsequent lines as data rows
            for j in range(i+1, len(lines)):
                data_line = lines[j].strip()

                # Skip empty lines
                if not data_line:
                    continue

                # Stop at metadata section (Servings, etc.)
                if 'Servings' in data_line or 'Serving Cost' in data_line:
                    break

                # Check if this is a notes line (starts with "Notes:")
                if data_line.startswith('Notes:'):
                    # Add note to the last ingredient if one exists
                    if len(table) > 1:  # Make sure we have at least header + 1 row
                        note_text = data_line.replace('Notes:', '').strip()
                        # Append note to last row (assuming 5th column for notes)
                        if len(table[-1]) == 4:  # If row has 4 columns, add 5th for note
                            table[-1].append(note_text)
                        print(f"  DEBUG: Added note to previous ingredient: {note_text}")
                    continue

                # Parse ingredient row using regex to split by money values and numbers
                # Pattern: ingredient_name (optional_label) $cost number+unit percentage% $cost
                # Updated to handle labels like "(Subrecipe)" in ingredient names
                match = re.match(r'(.+?)\s+\$?([\d.]+)\s+([\d.]+\w+)\s+(\d+%)\s+\$?([\d.]+)', data_line)
                if match:
                    ingredient = match.group(1).strip()
                    cu_cost = f"${match.group(2)}"
                    size_yield = match.group(3)
                    ru_cost = f"${match.group(5)}"
                    table.append([ingredient, cu_cost, size_yield, ru_cost, None])  # Add None for note initially
                    print(f"  DEBUG: Parsed row: {ingredient} | {cu_cost} | {size_yield} | {ru_cost}")
                else:
                    # Try alternative pattern without percentage (some rows might be missing it)
                    match_alt = re.match(r'(.+?)\s+\$?([\d.]+)\s+([\d.]+\w+)\s+\$?([\d.]+)', data_line)
                    if match_alt:
                        ingredient = match_alt.group(1).strip()
                        cu_cost = f"${match_alt.group(2)}"
                        size_yield = match_alt.group(3)
                        ru_cost = f"${match_alt.group(4)}"
                        table.append([ingredient, cu_cost, size_yield, ru_cost, None])  # Add None for note initially
                        print(f"  DEBUG: Parsed row (alt): {ingredient} | {cu_cost} | {size_yield} | {ru_cost}")
            break

    if not header_found:
        print(f"  DEBUG: Header not found in text")

    return table if len(table) > 1 else []


# ============================================================================
# Text Parsing Functions
# ============================================================================

def parse_dish_name(text: str) -> str:
    """
    Extract dish name from PDF text
    Format: 'LOCATION - Dish Name Company Name'
    Example: 'ATLANTA - Pure Celery E & Rose Wellness Company' returns 'Pure Celery'
    """
    lines = text.split('\n')
    if lines and '-' in lines[0]:
        # Extract everything after the first dash
        full_text = lines[0].split('-', 1)[1].strip()

        # The company name appears at the end - find it by checking common patterns
        # Look for company keywords that typically appear at the end
        company_keywords = ['Company', 'Inc', 'LLC', 'Corp', 'Ltd']

        # Split by spaces and work backwards to find where company name starts
        words = full_text.split()

        # Find the last occurrence of company keywords
        company_start_idx = None
        for i in range(len(words) - 1, -1, -1):
            if any(keyword in words[i] for keyword in company_keywords):
                # Found a company keyword, now go back to find where it starts
                # Typically it's "Name & Name Company" or "Name Company"
                # Look for '&' or capitalized words before this
                for j in range(i, -1, -1):
                    if j > 0 and words[j-1] == '&':
                        company_start_idx = j - 2  # Include word before '&'
                        break
                    elif j == 0 or (j > 0 and not words[j][0].isupper()):
                        company_start_idx = j
                        break
                else:
                    company_start_idx = 0
                break

        # Extract dish name (everything before company name)
        if company_start_idx is not None and company_start_idx > 0:
            dish_name = ' '.join(words[:company_start_idx]).strip()
            return dish_name

        return full_text
    return ''


def parse_business_name(text: str) -> str:
    """
    Extract business name from PDF text
    Format: 'LOCATION - Dish Name Company Name'
    Example: 'ATLANTA - Pure Celery E & Rose Wellness Company' returns 'E & Rose Wellness Company'
    """
    lines = text.split('\n')
    if lines and '-' in lines[0]:
        full_text = lines[0].split('-', 1)[1].strip()

        # Find company name at the end
        company_keywords = ['Company', 'Inc', 'LLC', 'Corp', 'Ltd']
        words = full_text.split()

        # Find the last occurrence of company keywords
        for i in range(len(words) - 1, -1, -1):
            if any(keyword in words[i] for keyword in company_keywords):
                # Find where company name starts
                for j in range(i, -1, -1):
                    if j > 0 and words[j-1] == '&':
                        return ' '.join(words[j-2:])
                    elif j == 0 or (j > 0 and not words[j][0].isupper()):
                        return ' '.join(words[j:])
                return ' '.join(words)

    return ''


def parse_servings(text: str) -> int:
    """Extract servings number from text"""
    match = re.search(r'Servings\s+(\d+)', text)
    return int(match.group(1)) if match else 0


def parse_shelf_life(text: str) -> Optional[int]:
    """Extract shelf life in days from text"""
    match = re.search(r'Shelf Life \(days\)\s+(\d+)', text)
    if match:
        return int(match.group(1))  # Return even if 0
    return None


def parse_menu_price(text: str) -> Optional[Decimal]:
    """Extract menu price from text"""
    match = re.search(r'Menu Price\s+\$?([\d.]+)', text)
    if match:
        return Decimal(match.group(1))
    return None


def parse_cost_percent(text: str) -> Optional[Decimal]:
    """Extract cost percentage from text"""
    match = re.search(r'Cost %\s+([\d.]+)', text)
    if match:
        return Decimal(match.group(1))
    return None


def parse_final_price(text: str) -> Optional[Decimal]:
    """Extract final price from text"""
    match = re.search(r'Final Price\s+\$?([\d.]+)', text)
    if match:
        return Decimal(match.group(1))
    return None


def parse_prep_time(text: str) -> Optional[int]:
    """Extract prep time in minutes from text"""
    match = re.search(r'Prep Time \(min\)\s+(\d+)', text)
    if match:
        return int(match.group(1))
    return None


def parse_storage_temp(text: str) -> Optional[int]:
    """Extract storage temperature in Fahrenheit from text"""
    match = re.search(r'Storage Temp \(F\)\s+(\d+)', text)
    if match:
        return int(match.group(1))
    return None


def parse_serving_cost(text: str) -> Optional[Decimal]:
    """Extract serving cost from text"""
    match = re.search(r'Serving Cost\s+\$?([\d.]+)', text)
    if match:
        return Decimal(match.group(1))
    return None


# ============================================================================
# Table Parsing Functions
# ============================================================================

def parse_quantity_unit(size_yield_str: str) -> Tuple[float, str]:
    """
    Parse quantity and unit from SIZE YIELD column
    Examples:
        '2.25lb' -> (2.25, 'lb')
        '12oz' -> (12.0, 'oz')
        '36orange' -> (36.0, 'orange')
    """
    if not size_yield_str:
        return 0.0, ''

    # Match number followed by unit
    match = re.match(r'([\d.]+)(\w+)', str(size_yield_str))
    if match:
        return float(match.group(1)), match.group(2)
    return 0.0, ''


def parse_cost(cost_str: str) -> Optional[Decimal]:
    """Parse cost string to Decimal (handles $3.11 or 3.11)"""
    if not cost_str or cost_str == '' or pd.isna(cost_str):
        return None

    # Remove $ sign and convert to Decimal
    cost_clean = str(cost_str).replace('$', '').strip()
    try:
        return Decimal(cost_clean) if cost_clean else None
    except:
        return None


def table_to_dataframe(table: List[List[str]]) -> pd.DataFrame:
    """
    Convert extracted table to pandas DataFrame
    First row is headers
    """
    if not table or len(table) < 2:
        return pd.DataFrame()

    headers = table[0]
    data = table[1:]
    return pd.DataFrame(data, columns=headers)


def extract_ingredient_rows(table: List[List[str]]) -> pd.DataFrame:
    """
    Extract ingredients table from PDF and return as DataFrame
    Assumes first row contains headers: ITEMS, CU COST, SIZE YIELD %, RU COST
    """
    df = table_to_dataframe(table)

    # Clean up: remove rows where ITEMS is empty
    if 'ITEMS' in df.columns:
        df = df[df['ITEMS'].notna() & (df['ITEMS'] != '')]

    return df


# ============================================================================
# Database Query Functions
# ============================================================================

def get_ingredient_id(cursor, ingredient_name: str) -> Optional[uuid.UUID]:
    """Check if ingredient exists and return its ID"""
    cursor.execute(
        "SELECT ingredient_id FROM ingredients WHERE LOWER(name) = LOWER(%s)",
        (ingredient_name,)
    )
    result = cursor.fetchone()
    return result[0] if result else None


def create_ingredient(cursor, ingredient_name: str) -> uuid.UUID:
    """Create new ingredient and return its ID"""
    new_id = uuid.uuid4()
    cursor.execute(
        "INSERT INTO ingredients (ingredient_id, name) VALUES (%s, %s)",
        (new_id, ingredient_name)
    )
    return new_id


def get_or_create_ingredient(cursor, ingredient_name: str) -> uuid.UUID:
    """Get existing ingredient or create new one"""
    existing = get_ingredient_id(cursor, ingredient_name)
    return existing if existing else create_ingredient(cursor, ingredient_name)


def get_business_id(cursor, business_name: str) -> Optional[uuid.UUID]:
    """Check if business exists and return its ID"""
    cursor.execute(
        "SELECT id FROM businesses WHERE LOWER(name) = LOWER(%s)",
        (business_name,)
    )
    result = cursor.fetchone()
    return result[0] if result else None


def create_business(cursor, business_name: str) -> uuid.UUID:
    """Create new business and return its ID"""
    new_id = uuid.uuid4()
    cursor.execute(
        "INSERT INTO businesses (id, name) VALUES (%s, %s)",
        (new_id, business_name)
    )
    return new_id


def get_or_create_business(cursor, business_name: str) -> uuid.UUID:
    """Get existing business or create new one"""
    existing = get_business_id(cursor, business_name)
    return existing if existing else create_business(cursor, business_name)


# ============================================================================
# Recipe Row Building
# ============================================================================

def clean_ingredient_name(ingredient_name: str) -> str:
    """
    Remove quantity+unit suffix from ingredient name
    Examples:
        'Celery 1bunch' -> 'Celery'
        '12 oz. juice bottles 1each' -> '12 oz. juice bottles'
        'Ginger, Fresh 1lb' -> 'Ginger, Fresh'
    """
    # Remove pattern like "1bunch", "1each", "1lb" at the end
    cleaned = re.sub(r'\s+\d+\w+$', '', ingredient_name)
    return cleaned.strip()


def build_recipe_row(
    ingredient_name: str,
    cu_cost: str,
    size_yield: str,
    ru_cost: str,
    ingredient_note: Optional[str],
    metadata: Dict,
    dish_id: int,
    business_id: int,
    ingredient_id: int,
    row_id: int
) -> Dict:
    """Build a single recipe row dictionary"""
    recipe_quantity, recipe_unit = parse_quantity_unit(size_yield)

    # Clean the ingredient name
    clean_name = clean_ingredient_name(ingredient_name)

    return {
        'id': row_id,
        'dish_id': dish_id,
        'dish_name': metadata['dish_name'],
        'servings': metadata['servings'],
        'ingredient_id': ingredient_id,
        'ingredient_name': clean_name,  # Use cleaned name
        'recipe_quantity': recipe_quantity,
        'recipe_unit': recipe_unit,
        'business_id': business_id,
        'IsFullRecipe': True,
        'IsSubRecipe': False,
        'shelf_life_days': metadata['shelf_life_days'],
        'cost_per_pack_unit': parse_cost(cu_cost),
        'ingredient_total_cost': parse_cost(ru_cost),
        'is_active': True,
        'menu_price': metadata['menu_price'],
        'cost_percent': metadata['cost_percent'],
        'final_price': metadata['final_price'],
        'prep_time_minutes': metadata['prep_time_minutes'],
        'storage_temp_fahrenheit': metadata['storage_temp_fahrenheit'],
        'serving_cost': metadata['serving_cost'],
        'Ingredient_note': ingredient_note
    }


def process_ingredients_table(
    ingredients_df: pd.DataFrame,
    metadata: Dict,
    dish_id: int,
    business_id: uuid.UUID,
    starting_row_id: int = 1
) -> List[Dict]:
    """
    Process each row in ingredients table and build recipe rows
    - Auto-increment integers: id (recipe row), dish_id
    - UUIDs: ingredient_id, business_id (global uniqueness)
    """
    recipe_rows = []
    row_id = starting_row_id

    for _, row in ingredients_df.iterrows():
        ingredient_name = row.get('ITEMS', '').strip()

        if not ingredient_name:
            continue

        # Generate UUIDs for globally unique entities
        ingredient_id = uuid.uuid4()

        # Build recipe row
        recipe_row = build_recipe_row(
            ingredient_name=ingredient_name,
            cu_cost=row.get('CU COST', ''),
            size_yield=row.get('SIZE YIELD %', ''),
            ru_cost=row.get('RU COST', ''),
            metadata=metadata,
            dish_id=dish_id,
            business_id=business_id,
            ingredient_id=ingredient_id,
            row_id=row_id  # Auto-increment integer
        )

        recipe_rows.append(recipe_row)
        row_id += 1

    return recipe_rows


def process_llm_recipe_data(
    recipe_data: Dict,
    dish_id: int,
    business_id: uuid.UUID,
    starting_row_id: int = 1
) -> List[Dict]:
    """
    Process LLM parsed data and build recipe rows
    """
    recipe_rows = []
    row_id = starting_row_id
    metadata = recipe_data['metadata']

    for ingredient in recipe_data['ingredients']:
        ingredient_name = ingredient.get('ingredient_name', '').strip()
        if not ingredient_name:
            continue

        # Generate UUIDs
        ingredient_id = uuid.uuid4()

        # Build recipe row
        recipe_row = {
            'id': row_id,
            'dish_id': dish_id,
            'dish_name': metadata.get('dish_name'),
            'servings': metadata.get('servings'),
            'ingredient_id': ingredient_id,
            'ingredient_name': ingredient_name,
            'recipe_quantity': ingredient.get('quantity'),
            'recipe_unit': ingredient.get('unit'),
            'business_id': business_id,
            'IsFullRecipe': True,
            'IsSubRecipe': False,
            'shelf_life_days': metadata.get('shelf_life_days'),
            'cost_per_pack_unit': None,  # LLM doesn't extract this explicitly yet
            'ingredient_total_cost': ingredient.get('cost'),
            'is_active': True,
            'menu_price': metadata.get('menu_price'),
            'cost_percent': metadata.get('cost_percent'),
            'final_price': metadata.get('final_price'),
            'prep_time_minutes': metadata.get('prep_time_minutes'),
            'storage_temp_fahrenheit': metadata.get('storage_temp_fahrenheit'),
            'serving_cost': metadata.get('serving_cost'),
            'Ingredient_note': ingredient.get('note')
        }

        recipe_rows.append(recipe_row)
        row_id += 1

    return recipe_rows


def save_recipe_rows_to_csv(rows: List[Dict], output_path: str) -> None:
    """
    Save recipe rows to CSV file
    """
    if not rows:
        print("  No rows to save")
        return

    # Convert to DataFrame
    df = pd.DataFrame(rows)

    # Convert UUID objects to strings for CSV
    for col in df.columns:
        if df[col].dtype == 'object':
            df[col] = df[col].apply(lambda x: str(x) if isinstance(x, uuid.UUID) else x)

    # Save to CSV
    df.to_csv(output_path, index=False)
    print(f"  DEBUG: Saved {len(rows)} rows to '{output_path}'")


# ============================================================================
# Main Processing Pipeline
# ============================================================================

def parse_recipe_to_csv(
    pdf_path: str,
    output_csv: str,
    business_name: Optional[str] = None
) -> None:
    """
    Main pipeline: Extract PDF -> Parse data -> Save to CSV

    Args:
        pdf_path: Path to PDF file
        output_csv: Path to output CSV file
        business_name: Optional override for business name
    """
    print(f"Processing: {pdf_path}")

    # Step 1: Extract text from PDF
    text = extract_text_from_pdf(pdf_path)

    # Step 2: Parse with LLM
    print("  Parsing with Local LLM...")
    recipe_data = parse_recipe_with_llm(text)
    
    if not recipe_data:
        print("✗ Could not parse recipe with LLM")
        return

    print(f"  Dish: {recipe_data['metadata'].get('dish_name')}")
    print(f"  Found {len(recipe_data['ingredients'])} ingredients")

    # Step 3: Build recipe rows
    business_id = uuid.uuid4()
    dish_id = 1  

    recipe_rows = process_llm_recipe_data(
        recipe_data, dish_id, business_id
    )

    # Step 4: Save to CSV
    save_recipe_rows_to_csv(recipe_rows, output_csv)

    print(f"✓ Saved {len(recipe_rows)} recipe rows to CSV")


def parse_multiple_recipes_to_csv(
    pdf_paths: List[str],
    output_csv: str,
    business_name: Optional[str] = None
) -> None:
    """Process multiple PDF files and save all to single CSV"""
    total = len(pdf_paths)
    all_rows = []
    dish_counter = 1  # Auto-increment dish IDs

    for i, pdf_path in enumerate(pdf_paths, 1):
        print(f"\n[{i}/{total}]")
        try:
            # Process individual PDF (extract and parse only)
            # Process individual PDF
            text = extract_text_from_pdf(pdf_path)
            
            # Parse with LLM
            recipe_data = parse_recipe_with_llm(text)

            if not recipe_data:
                print(f"  ✗ Skipping {pdf_path} - LLM parsing failed")
                continue

            # UUIDs for businesses (global), integers for dishes (local)
            business_id = uuid.uuid4()
            dish_id = dish_counter
            dish_counter += 1

            recipe_rows = process_llm_recipe_data(recipe_data, dish_id, business_id)
            all_rows.extend(recipe_rows)

        except Exception as e:
            print(f"  ✗ Error: {e}")

    # Save all rows to single CSV
    if all_rows:
        save_recipe_rows_to_csv(all_rows, output_csv)
        print(f"\n✓ Saved {len(all_rows)} total rows from {total} PDFs")


def mark_sub_recipes(all_rows: List[Dict]) -> List[Dict]:
    """
    Second pass: Mark ingredients as sub-recipes if their name matches any dish name

    Args:
        all_rows: List of all recipe rows

    Returns:
        Updated list with IsSubRecipe flags set correctly
    """
    # Collect all dish names (case-insensitive)
    dish_names = {row['dish_name'].lower() for row in all_rows}

    # Create a mapping of dish_name -> dish_id for sub-recipe linking
    dish_name_to_id = {}
    for row in all_rows:
        # Normalize dish name: lower case, strip
        clean_dish_name = row['dish_name'].lower().strip()
        if clean_dish_name not in dish_name_to_id:
            dish_name_to_id[clean_dish_name] = row['dish_id']

    sub_recipe_count = 0

    # Mark sub-recipes
    for row in all_rows:
        # Normalize ingredient name
        ingredient_name_lower = row['ingredient_name'].lower().strip()

        # Check if this ingredient matches any dish name
        # Try exact match first
        if ingredient_name_lower in dish_name_to_id:
            row['IsSubRecipe'] = True
            row['full_recipe_id'] = dish_name_to_id.get(ingredient_name_lower)
            sub_recipe_count += 1
        # Optional: Try matching with "recipe" suffix removed or added if needed
        # But for now, stick to exact name match as requested


    if sub_recipe_count > 0:
        print(f"  Marked {sub_recipe_count} ingredients as sub-recipes")

    return all_rows


def parse_zip_to_csv(
    zip_path: str,
    output_csv: str,
    business_name: Optional[str] = None
) -> None:
    """
    Extract all PDFs from a zip file and process them into a single CSV

    Args:
        zip_path: Path to zip file containing PDFs
        output_csv: Path to output CSV file
        business_name: Optional override for business name
    """
    print(f"Processing ZIP file: {zip_path}")

    all_rows = []
    dish_counter = 1

    # Create a temporary directory to extract PDFs
    with tempfile.TemporaryDirectory() as temp_dir:
        # Extract zip file
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            zip_ref.extractall(temp_dir)
            print(f"  Extracted files to temporary directory")

        # Find all PDF files in the extracted directory
        pdf_files = []
        for root, dirs, files in os.walk(temp_dir):
            for file in files:
                if file.lower().endswith('.pdf'):
                    pdf_files.append(os.path.join(root, file))

        total = len(pdf_files)
        print(f"  Found {total} PDF files in zip\n")

        # Process each PDF
        for i, pdf_path in enumerate(sorted(pdf_files), 1):
            # Get relative path within the zip
            rel_path = os.path.relpath(pdf_path, temp_dir)
            print(f"[{i}/{total}] {rel_path}")
            try:
                # Extract and parse PDF
                # Extract and parse PDF
                text = extract_text_from_pdf(pdf_path)
                
                # Parse with LLM
                recipe_data = parse_recipe_with_llm(text)

                if not recipe_data:
                    print(f"  ✗ Skipping - LLM parsing failed: {rel_path}")
                    continue

                print(f"  Dish: {recipe_data['metadata'].get('dish_name')}")
                print(f"  Found {len(recipe_data['ingredients'])} ingredients")

                # Generate IDs
                business_id = uuid.uuid4()
                dish_id = dish_counter
                dish_counter += 1

                recipe_rows = process_llm_recipe_data(recipe_data, dish_id, business_id)
                all_rows.extend(recipe_rows)
                print(f"  ✓ Added {len(recipe_rows)} rows")

            except Exception as e:
                print(f"  ✗ Error: {e}")
                continue

    # Second pass: Mark sub-recipes
    if all_rows:
        print("\nDetecting sub-recipes...")
        all_rows = mark_sub_recipes(all_rows)

    # Save all rows to CSV
    if all_rows:
        save_recipe_rows_to_csv(all_rows, output_csv)
        print(f"\n✓ Successfully saved {len(all_rows)} total rows from {total} PDFs to '{output_csv}'")
    else:
        print(f"\n✗ No recipes were successfully parsed")


# ============================================================================
# Example Usage
# ============================================================================

if __name__ == "__main__":
    # Configuration
    PDF_PATH = "sample_data/page_14.pdf"
    ZIP_PATH = "sample_data/E & Rose Wellness Company - Foodager - Booster Bites-20251205T181222Z-1-001.zip"
    OUTPUT_CSV = "output/recipes.csv"
    BUSINESS_NAME = "E & Rose Wellness Company"

    # Choose one of the following options:

    # Option 1: Process a single PDF
    # try:
    #     parse_recipe_to_csv(PDF_PATH, OUTPUT_CSV, BUSINESS_NAME)
    #     print("\n✓ Recipe successfully exported to CSV")
    # except Exception as e:
    #     print(f"\n✗ Error: {e}")
    #     import traceback
    #     traceback.print_exc()


    # Option 2: Process all PDFs from a zip file
    try:
        parse_zip_to_csv(ZIP_PATH, OUTPUT_CSV, BUSINESS_NAME)
    except Exception as e:
        print(f"\n✗ Error: {e}")
        import traceback
        traceback.print_exc()

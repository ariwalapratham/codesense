"""Test script for parser service."""

import sys
from pathlib import Path

# Add backend to path
backend_path = Path(__file__).parent / "backend"
sys.path.insert(0, str(backend_path))

# Import directly to avoid settings initialization
sys.path.insert(0, str(backend_path / "services"))
from parser_service import ParserService

def test_python_parser():
    """Test Python code parsing."""
    print("Testing Python parser...")
    print("-" * 50)
    
    python_code = """
import os
from typing import List, Dict

def calculate_sum(numbers: List[int]) -> int:
    \"\"\"Calculate sum of numbers.\"\"\"
    return sum(numbers)

def process_data(data: Dict[str, any]) -> Dict:
    result = {}
    for key, value in data.items():
        result[key] = value * 2
    return result

class Calculator:
    \"\"\"A simple calculator class.\"\"\"
    
    def __init__(self):
        self.value = 0
    
    def add(self, x: int) -> int:
        self.value += x
        return self.value
    
    def multiply(self, a: int, b: int) -> int:
        return a * b
"""
    
    parser = ParserService()
    result = parser.parse_file(Path("test.py"), python_code)
    
    print(f"Language: {result['language']}")
    print(f"Functions found: {len(result['functions'])}")
    print(f"Classes found: {len(result['classes'])}")
    print(f"Imports found: {len(result['imports'])}")
    print()
    
    print("Functions:")
    for func in result['functions']:
        print(f"  - {func['name']} (lines {func['start_line']}-{func['end_line']})")
        print(f"    Signature: {func.get('signature', 'N/A')}")
    
    print("\nClasses:")
    for cls in result['classes']:
        print(f"  - {cls['name']} (lines {cls['start_line']}-{cls['end_line']})")
    
    print("\nImports:")
    for imp in result['imports']:
        print(f"  - {imp['text']} (line {imp['line']})")
    
    return result

def test_javascript_parser():
    """Test JavaScript code parsing."""
    print("\n\nTesting JavaScript parser...")
    print("-" * 50)
    
    js_code = """
import { useState, useEffect } from 'react';

function calculateSum(numbers) {
    return numbers.reduce((a, b) => a + b, 0);
}

const processData = (data) => {
    const result = {};
    for (const key in data) {
        result[key] = data[key] * 2;
    }
    return result;
};

class Calculator {
    constructor() {
        this.value = 0;
    }
    
    add(x) {
        this.value += x;
        return this.value;
    }
    
    multiply(a, b) {
        return a * b;
    }
}
"""
    
    parser = ParserService()
    result = parser.parse_file(Path("test.js"), js_code)
    
    print(f"Language: {result['language']}")
    print(f"Functions found: {len(result['functions'])}")
    print(f"Classes found: {len(result['classes'])}")
    print(f"Imports found: {len(result['imports'])}")
    print()
    
    print("Functions:")
    for func in result['functions']:
        print(f"  - {func['name'] or 'anonymous'} (lines {func['start_line']}-{func['end_line']})")
    
    print("\nClasses:")
    for cls in result['classes']:
        print(f"  - {cls['name']} (lines {cls['start_line']}-{cls['end_line']})")
    
    print("\nImports:")
    for imp in result['imports']:
        print(f"  - {imp['text']} (line {imp['line']})")
    
    return result

if __name__ == "__main__":
    try:
        python_result = test_python_parser()
        js_result = test_javascript_parser()
        
        print("\n" + "=" * 50)
        print("✅ Parser service test completed!")
        print(f"Python: {len(python_result['functions'])} functions, {len(python_result['classes'])} classes")
        print(f"JavaScript: {len(js_result['functions'])} functions, {len(js_result['classes'])} classes")
        
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()


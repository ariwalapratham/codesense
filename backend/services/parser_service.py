"""Parser service for extracting code structures using tree-sitter."""

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    from tree_sitter_languages import get_parser
    TREE_SITTER_AVAILABLE = True
except ImportError:
    get_parser = None
    TREE_SITTER_AVAILABLE = False

# Setup logger
logger = logging.getLogger(__name__)


class ParserService:
    """Service for parsing code files and extracting structures."""

    def __init__(self):
        """Initialize parser service with language parsers."""
        self.parsers = {}

        # Map file extensions to tree-sitter language names
        self.supported_languages = {
            'py': 'python', 'js': 'javascript', 'jsx': 'javascript',
            'ts': 'typescript', 'tsx': 'typescript', 'java': 'java',
            'go': 'go', 'cpp': 'cpp', 'c': 'c', 'cxx': 'cpp', 'cc': 'cpp',
            'rb': 'ruby', 'php': 'php', 'cs': 'csharp', 'rs': 'rust',
            'swift': 'swift', 'kt': 'kotlin', 'scala': 'scala', 'r': 'r',
            'sh': 'bash', 'bash': 'bash', 'zsh': 'bash', 'sql': 'sql',
            'html': 'html', 'css': 'css', 'json': 'json', 'yaml': 'yaml',
            'yml': 'yaml', 'toml': 'toml', 'xml': 'xml', 'md': 'markdown',
            'markdown': 'markdown',
        }

        # Configuration for function extraction patterns
        self.function_config = {
            'python': {
                'node_types': ['function_definition'],
                'name_types': ['identifier'],
                'param_types': ['parameters'],
                'body_types': ['block'],
            },
            'javascript': {
                'node_types': ['function_declaration', 'arrow_function', 'function_expression'],
                'name_types': ['identifier'],
                'param_types': ['formal_parameters'],
                'body_types': ['statement_block', 'expression', 'statement'],
            },
            'typescript': {
                'node_types': ['function_declaration', 'arrow_function', 'function_expression'],
                'name_types': ['identifier'],
                'param_types': ['formal_parameters'],
                'body_types': ['statement_block', 'expression', 'statement'],
            },
            'java': {
                'node_types': ['method_declaration'],
                'name_types': ['identifier'],
                'param_types': ['formal_parameters'],
                'body_types': ['block'],
            },
            'go': {
                'node_types': ['function_declaration', 'method_declaration'],
                'name_types': ['identifier'],
                'param_types': ['parameter_list'],
                'body_types': ['block'],
            },
            'cpp': {
                'node_types': ['function_definition'],
                'name_types': ['identifier'],
                'param_types': ['parameter_list'],
                'body_types': ['compound_statement'],
                'nested_name': 'function_declarator',
            },
            'c': {
                'node_types': ['function_definition'],
                'name_types': ['identifier'],
                'param_types': ['parameter_list'],
                'body_types': ['compound_statement'],
                'nested_name': 'function_declarator',
            },
            'rust': {
                'node_types': ['function_item'],
                'name_types': ['identifier'],
                'param_types': ['parameters'],
                'body_types': ['block'],
            },
            'ruby': {
                'node_types': ['method'],
                'name_types': ['identifier'],
                'param_types': ['method_parameters'],
                'body_types': ['body_statement', 'block'],
            },
            'php': {
                'node_types': ['function_definition', 'method_declaration'],
                'name_types': ['name'],
                'param_types': ['formal_parameters'],
                'body_types': ['compound_statement'],
            },
            'csharp': {
                'node_types': ['method_declaration'],
                'name_types': ['identifier'],
                'param_types': ['formal_parameters'],
                'body_types': ['block'],
            },
            'swift': {
                'node_types': ['function_declaration'],
                'name_types': ['simple_identifier'],
                'param_types': ['parameter_list'],
                'body_types': ['function_body'],
            },
            'kotlin': {
                'node_types': ['function_declaration'],
                'name_types': ['simple_identifier'],
                'param_types': ['function_value_parameters'],
                'body_types': ['function_body'],
            },
            'scala': {
                'node_types': ['function_declaration'],
                'name_types': ['identifier'],
                'param_types': ['parameter_clauses'],
                'body_types': ['block'],
            },
        }

        # Configuration for class extraction patterns
        self.class_config = {
            'python': {
                'node_types': ['class_definition'],
                'name_types': ['identifier'],
                'body_types': ['block'],
            },
            'javascript': {
                'node_types': ['class_declaration'],
                'name_types': ['identifier', 'type_identifier'],
                'body_types': ['class_body'],
            },
            'typescript': {
                'node_types': ['class_declaration'],
                'name_types': ['identifier', 'type_identifier'],
                'body_types': ['class_body'],
            },
            'java': {
                'node_types': ['class_declaration'],
                'name_types': ['identifier', 'type_identifier'],
                'body_types': ['class_body'],
            },
            'go': {
                'node_types': ['type_declaration'],
                'name_types': ['type_identifier'],
                'body_types': ['struct_type'],
                'nested_name': 'type_spec',
            },
            'cpp': {
                'node_types': ['class_specifier'],
                'name_types': ['type_identifier', 'identifier'],
                'body_types': ['field_declaration_list'],
            },
            'c': {
                'node_types': ['struct_specifier'],
                'name_types': ['type_identifier', 'identifier'],
                'body_types': ['field_declaration_list'],
            },
            'rust': {
                'node_types': ['struct_item', 'impl_item'],
                'name_types': ['type_identifier'],
                'body_types': ['field_declaration_list'],
            },
            'ruby': {
                'node_types': ['class'],
                'name_types': ['constant'],
                'body_types': ['body_statement'],
            },
            'php': {
                'node_types': ['class_declaration'],
                'name_types': ['name'],
                'body_types': ['declaration_list'],
            },
            'csharp': {
                'node_types': ['class_declaration'],
                'name_types': ['identifier', 'type_identifier'],
                'body_types': ['class_body'],
            },
            'swift': {
                'node_types': ['class_declaration'],
                'name_types': ['type_identifier'],
                'body_types': ['class_body'],
            },
            'kotlin': {
                'node_types': ['class_declaration'],
                'name_types': ['simple_identifier', 'type_identifier'],
                'body_types': ['class_body'],
            },
            'scala': {
                'node_types': ['class_declaration'],
                'name_types': ['type_identifier'],
                'body_types': ['class_body'],
            },
        }

        # Configuration for import extraction patterns
        self.import_config = {
            'python': ['import_statement', 'import_from_statement'],
            'javascript': ['import_statement'],
            'typescript': ['import_statement'],
            'java': ['import_declaration'],
            'go': ['import_declaration'],
            'cpp': ['preproc_include'],
            'c': ['preproc_include'],
            'ruby': ['call'],  # Ruby uses require/load
            'php': ['namespace_use_declaration'],
            'csharp': ['using_directive'],
            'rust': ['use_declaration'],
            'swift': ['import_declaration'],
            'kotlin': ['import_list', 'import_header'],
            'scala': ['import_declaration'],
        }

        if not TREE_SITTER_AVAILABLE:
            logger.warning("tree-sitter-languages not available. Parsing will be limited.")

    def _get_language(self, file_path: Path) -> Optional[str]:
        """Get tree-sitter language name from file extension."""
        ext = file_path.suffix.lstrip('.')
        return self.supported_languages.get(ext.lower())

    def _get_parser(self, language: str):
        """Get or create parser for a language using tree-sitter-languages."""
        if not TREE_SITTER_AVAILABLE or get_parser is None:
            return None

        if language not in self.parsers:
            try:
                parser = get_parser(language)
                self.parsers[language] = parser
                logger.debug("Loaded parser for language: %s", language)
            except (ValueError, Exception) as e:
                logger.warning("Failed to load parser for %s: %s", language, e)
                return None

        return self.parsers.get(language)

    def _get_node_text(self, node, code_text: str) -> str:
        """Extract text content from a tree-sitter node."""
        if node is None:
            return ""
        return code_text[node.start_byte:node.end_byte]

    def _find_nodes_by_types(
        self, node,
        types: Dict[str, List[str]],
        nested_type: Optional[str] = None,
        include_params: bool = True) -> Tuple[Optional[Any], Optional[Any], Optional[Any]]:
        """
        Find child nodes matching specified types.

        Args:
            node: Tree-sitter node to search
            types: Configuration dict with 'name_types', 'param_types', 'body_types'
            nested_type: Optional nested node type to search first (e.g., 'function_declarator')
            include_params: Whether to search for parameter nodes

        Returns:
            Tuple of (name_node, param_node, body_node) or (name_node, None, body_node)
            if include_params=False
        """
        name_node = None
        param_node = None
        body_node = None

        # Handle nested structures (e.g., C++ function_declarator)
        if nested_type:
            for child in node.children:
                if child.type == nested_type:
                    nested_name, nested_param, _ = self._find_nodes_by_types(
                        child, types, include_params=include_params)
                    name_node = nested_name or name_node
                    param_node = nested_param or param_node
                    break

        # Search direct children
        for child in node.children:
            child_type = child.type
            if child_type in types.get('name_types', []):
                name_node = name_node or child
            elif include_params and child_type in types.get('param_types', []):
                param_node = child
            elif child_type in types.get('body_types', []):
                body_node = child

        return name_node, param_node, body_node

    def _extract_functions(self, tree, code_text: str, language: str) -> List[Dict]:
        """Extract function definitions from parsed AST using tree traversal."""
        functions = []
        config = self.function_config.get(language)

        if not config:
            # Generic fallback
            config = {
                'node_types': ['function_definition', 'function_declaration', 'method_declaration'],
                'name_types': ['identifier', 'type_identifier'],
                'param_types': ['parameters', 'formal_parameters', 'parameter_list'],
                'body_types': ['block', 'statement_block', 'body'],
            }

        node_types = config['node_types']
        nested_name = config.get('nested_name')

        def traverse(node):
            if node.type in node_types:
                name_node, params_node, body_node = self._find_nodes_by_types(
                    node, config, nested_name
                )

                func_name = self._get_node_text(name_node, code_text) if name_node else None

                # Include anonymous functions (arrow functions, function expressions)
                if func_name or node.type in ['arrow_function', 'function_expression']:
                    functions.append({
                        'name': func_name or 'anonymous',
                        'signature': (
                            self._get_node_text(params_node, code_text)
                            if params_node else ''
                        ),
                        'body': (
                            self._get_node_text(body_node, code_text)
                            if body_node else self._get_node_text(node, code_text)
                        ),
                        'start_line': node.start_point[0] + 1,
                        'end_line': node.end_point[0] + 1,
                    })

            for child in node.children:
                traverse(child)

        try:
            traverse(tree.root_node)
        except Exception as e:
            logger.warning("Error extracting functions for %s: %s", language, e)

        return functions

    def _extract_classes(self, tree, code_text: str, language: str) -> List[Dict]:
        """Extract class definitions from parsed AST using tree traversal."""
        classes = []
        config = self.class_config.get(language)

        if not config:
            # Generic fallback
            config = {
                'node_types': ['class_declaration', 'class_definition'],
                'name_types': ['identifier', 'type_identifier', 'constant'],
                'body_types': ['block', 'class_body', 'body'],
            }

        node_types = config['node_types']
        nested_name = config.get('nested_name')

        def traverse(node):
            if node.type in node_types:
                # For Go structs, need special handling (nested in type_spec)
                target_node = node
                if language == 'go' and node.type == 'type_declaration':
                    # Find type_spec child for Go structs
                    for child in node.children:
                        if child.type == 'type_spec':
                            target_node = child
                            break

                name_node, _, body_node = self._find_nodes_by_types(
                    target_node, config, nested_name, include_params=False
                )
                class_name = self._get_node_text(name_node, code_text) if name_node else None

                if class_name:
                    classes.append({
                        'name': class_name,
                        'body': (
                            self._get_node_text(body_node, code_text)
                            if body_node else
                            self._get_node_text(target_node, code_text)
                        ),
                        'start_line': target_node.start_point[0] + 1,
                        'end_line': target_node.end_point[0] + 1,
                    })

            for child in node.children:
                traverse(child)

        try:
            traverse(tree.root_node)
        except Exception as e:
            logger.warning("Error extracting classes for %s: %s", language, e)

        return classes

    def _extract_imports(self, tree, code_text: str, language: str) -> List[Dict]:
        """Extract import statements from parsed AST using tree traversal."""
        imports = []
        node_types = self.import_config.get(
            language,
            ['import_statement', 'import_declaration', 'import', 'use_declaration']
        )

        def traverse(node):
            if node.type in node_types:
                import_text = self._get_node_text(node, code_text).strip()
                imports.append({
                    'text': import_text,
                    'line': node.start_point[0] + 1,
                })

            for child in node.children:
                traverse(child)

        try:
            traverse(tree.root_node)
        except Exception as e:
            logger.warning("Error extracting imports for %s: %s", language, e)

        return imports

    def parse_file(self, file_path: Path, code_text: str, language: Optional[str] = None) -> Dict:
        """
        Parse a code file and extract structures.

        Returns:
            Dictionary with parsed structures:
            {
                "file_path": str,
                "language": str,
                "functions": List[Dict],
                "classes": List[Dict],
                "imports": List[Dict],
                "code_text": str
            }
        """
        if language is None:
            language = self._get_language(file_path)

        # Default empty result structure
        empty_result = {
            "file_path": str(file_path),
            "language": language or file_path.suffix.lstrip('.') or "unknown",
            "functions": [],
            "classes": [],
            "imports": [],
            "code_text": code_text
        }

        if language is None:
            logger.debug("Unsupported language for file: %s", file_path)
            return empty_result

        try:
            parser = self._get_parser(language)
            if parser is None:
                logger.warning("Parser not available for language: %s", language)
                return empty_result

            tree = parser.parse(bytes(code_text, "utf8"))

            functions = self._extract_functions(tree, code_text, language)
            classes = self._extract_classes(tree, code_text, language)
            imports = self._extract_imports(tree, code_text, language)

            logger.debug(
                "Parsed %s: %d functions, %d classes, %d imports",
                file_path, len(functions), len(classes), len(imports)
            )

            return {
                "file_path": str(file_path),
                "language": language,
                "functions": functions,
                "classes": classes,
                "imports": imports,
                "code_text": code_text
            }

        except Exception as e:
            logger.error("Error parsing file %s: %s", file_path, e, exc_info=True)
            return empty_result

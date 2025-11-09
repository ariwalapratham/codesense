"""Parser service for extracting code structures using tree-sitter."""

import logging
from pathlib import Path
from typing import Dict, List, Optional

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
        # tree-sitter-languages supports many languages
        self.supported_languages = {
            'py': 'python',
            'js': 'javascript',
            'jsx': 'javascript',
            'ts': 'typescript',
            'tsx': 'typescript',
            'java': 'java',
            'go': 'go',
            'cpp': 'cpp',
            'c': 'c',
            'cxx': 'cpp',
            'cc': 'cpp',
            'rb': 'ruby',
            'php': 'php',
            'cs': 'csharp',
            'rs': 'rust',
            'swift': 'swift',
            'kt': 'kotlin',
            'scala': 'scala',
            'r': 'r',
            'sh': 'bash',
            'bash': 'bash',
            'zsh': 'bash',
            'sql': 'sql',
            'html': 'html',
            'css': 'css',
            'json': 'json',
            'yaml': 'yaml',
            'yml': 'yaml',
            'toml': 'toml',
            'xml': 'xml',
            'md': 'markdown',
            'markdown': 'markdown',
        }
        
        if not TREE_SITTER_AVAILABLE:
            logger.warning("tree-sitter-languages not available. Parsing will be limited.")
    
    def _get_language(self, file_path: Path) -> Optional[str]:
        """
        Get tree-sitter language name from file extension.
        
        Args:
            file_path: Path to the file
            
        Returns:
            Language name or None if unsupported
        """
        ext = file_path.suffix.lstrip('.')
        return self.supported_languages.get(ext.lower())
    
    def _get_parser(self, language: str):
        """
        Get or create parser for a language using tree-sitter-languages.
        
        Args:
            language: Language name (e.g., 'python', 'javascript')
            
        Returns:
            Parser instance or None if not available
        """
        if not TREE_SITTER_AVAILABLE or get_parser is None:
            return None
            
        if language not in self.parsers:
            try:
                # get_parser returns a ready-to-use Parser instance
                parser = get_parser(language)
                self.parsers[language] = parser
                logger.debug("Loaded parser for language: %s", language)
            except (ValueError, Exception) as e:
                logger.warning("Failed to load parser for %s: %s", language, e)
                return None
        
        return self.parsers.get(language)
    
    def _get_node_text(self, node, code_text: str) -> str:
        """
        Extract text content from a tree-sitter node.
        
        Args:
            node: Tree-sitter node
            code_text: Full source code text
            
        Returns:
            Text content of the node
        """
        start_byte = node.start_byte
        end_byte = node.end_byte
        return code_text[start_byte:end_byte]
    
    def _find_child_by_type(self, node, node_type: str):
        """
        Find a child node by type.
        
        Args:
            node: Parent node
            node_type: Type to search for
            
        Returns:
            Child node or None
        """
        for child in node.children:
            if child.type == node_type:
                return child
        return None
    
    def _extract_functions(self, tree, code_text: str, language: str) -> List[Dict]:
        """
        Extract function definitions from parsed AST using tree traversal.
        
        Args:
            tree: Tree-sitter tree
            code_text: Full source code text
            language: Programming language
            
        Returns:
            List of function dictionaries
        """
        functions = []
        
        # Language-specific node types for functions
        function_types = {
            'python': ['function_definition'],
            'javascript': ['function_declaration', 'arrow_function', 'function_expression'],
            'typescript': ['function_declaration', 'arrow_function', 'function_expression'],
            'java': ['method_declaration'],
            'go': ['function_declaration', 'method_declaration'],
            'cpp': ['function_definition'],
            'c': ['function_definition'],
            'ruby': ['method'],
            'php': ['function_definition', 'method_declaration'],
            'csharp': ['method_declaration'],
            'rust': ['function_item'],
            'swift': ['function_declaration'],
            'kotlin': ['function_declaration'],
            'scala': ['function_declaration'],
        }
        
        node_types = function_types.get(language, ['function_definition', 'function_declaration', 'method_declaration'])
        
        def traverse(node):
            """Recursively traverse the tree to find functions."""
            if node.type in node_types:
                # Extract function information
                name_node = None
                params_node = None
                body_node = None
                
                # Find name, parameters, and body nodes based on language
                if language == 'python':
                    for child in node.children:
                        if child.type == 'identifier':
                            name_node = child
                        elif child.type == 'parameters':
                            params_node = child
                        elif child.type == 'block':
                            body_node = child
                elif language in ['javascript', 'typescript']:
                    for child in node.children:
                        if child.type == 'identifier' and name_node is None:
                            name_node = child
                        elif child.type == 'formal_parameters':
                            params_node = child
                        elif child.type == 'statement_block':
                            body_node = child
                        elif child.type in ['expression', 'statement'] and body_node is None:
                            # Arrow function body
                            body_node = child
                elif language in ['java', 'csharp', 'kotlin', 'swift']:
                    for child in node.children:
                        if child.type == 'identifier' and name_node is None:
                            name_node = child
                        elif child.type == 'formal_parameters':
                            params_node = child
                        elif child.type == 'block':
                            body_node = child
                elif language == 'go':
                    for child in node.children:
                        if child.type == 'identifier' and name_node is None:
                            name_node = child
                        elif child.type == 'parameter_list':
                            params_node = child
                        elif child.type == 'block':
                            body_node = child
                elif language in ['cpp', 'c']:
                    for child in node.children:
                        if child.type == 'function_declarator':
                            for grandchild in child.children:
                                if grandchild.type == 'identifier' and name_node is None:
                                    name_node = grandchild
                                elif grandchild.type == 'parameter_list':
                                    params_node = grandchild
                        elif child.type == 'compound_statement':
                            body_node = child
                elif language == 'rust':
                    for child in node.children:
                        if child.type == 'identifier' and name_node is None:
                            name_node = child
                        elif child.type == 'parameters':
                            params_node = child
                        elif child.type == 'block':
                            body_node = child
                else:
                    # Generic fallback: try to find common patterns
                    for child in node.children:
                        if child.type in ['identifier', 'type_identifier'] and name_node is None:
                            name_node = child
                        elif child.type in ['parameters', 'formal_parameters', 'parameter_list']:
                            params_node = child
                        elif child.type in ['block', 'statement_block', 'body']:
                            body_node = child
                
                # Extract function name
                func_name = self._get_node_text(name_node, code_text) if name_node else None
                
                # For arrow functions or anonymous functions without names
                if func_name or node.type in ['arrow_function', 'function_expression']:
                    functions.append({
                        'name': func_name or 'anonymous',
                        'signature': self._get_node_text(params_node, code_text) if params_node else '',
                        'body': self._get_node_text(body_node, code_text) if body_node else self._get_node_text(node, code_text),
                        'start_line': node.start_point[0] + 1,
                        'end_line': node.end_point[0] + 1,
                    })
            
            # Recursively traverse children
            for child in node.children:
                traverse(child)
        
        try:
            traverse(tree.root_node)
        except Exception as e:
            logger.warning("Error extracting functions for %s: %s", language, e)
        
        return functions
    
    def _extract_classes(self, tree, code_text: str, language: str) -> List[Dict]:
        """
        Extract class definitions from parsed AST using tree traversal.
        
        Args:
            tree: Tree-sitter tree
            code_text: Full source code text
            language: Programming language
            
        Returns:
            List of class dictionaries
        """
        classes = []
        
        # Language-specific node types for classes
        class_types = {
            'python': ['class_definition'],
            'javascript': ['class_declaration'],
            'typescript': ['class_declaration'],
            'java': ['class_declaration'],
            'go': ['type_declaration'],  # Go structs
            'cpp': ['class_specifier'],
            'c': ['struct_specifier'],
            'ruby': ['class'],
            'php': ['class_declaration'],
            'csharp': ['class_declaration'],
            'rust': ['struct_item', 'impl_item'],
            'swift': ['class_declaration'],
            'kotlin': ['class_declaration'],
            'scala': ['class_declaration'],
        }
        
        node_types = class_types.get(language, ['class_declaration', 'class_definition', 'class_declaration'])
        
        def traverse(node):
            """Recursively traverse the tree to find classes."""
            if node.type in node_types:
                # Extract class information
                name_node = None
                body_node = None
                
                # Find name and body nodes based on language
                if language == 'python':
                    for child in node.children:
                        if child.type == 'identifier':
                            name_node = child
                        elif child.type == 'block':
                            body_node = child
                elif language in ['javascript', 'typescript', 'java', 'php', 'csharp', 'swift', 'kotlin', 'scala']:
                    for child in node.children:
                        if child.type in ['identifier', 'type_identifier'] and name_node is None:
                            name_node = child
                        elif child.type == 'class_body':
                            body_node = child
                elif language == 'go':
                    # Go structs are nested in type_declaration > type_spec
                    for child in node.children:
                        if child.type == 'type_spec':
                            for grandchild in child.children:
                                if grandchild.type == 'type_identifier':
                                    name_node = grandchild
                                elif grandchild.type == 'struct_type':
                                    body_node = grandchild
                elif language in ['cpp', 'c']:
                    for child in node.children:
                        if child.type in ['type_identifier', 'identifier'] and name_node is None:
                            name_node = child
                        elif child.type in ['field_declaration_list', 'field_declaration']:
                            body_node = child
                elif language == 'rust':
                    for child in node.children:
                        if child.type == 'type_identifier' and name_node is None:
                            name_node = child
                        elif child.type == 'field_declaration_list':
                            body_node = child
                elif language == 'ruby':
                    for child in node.children:
                        if child.type == 'constant' and name_node is None:
                            name_node = child
                        elif child.type == 'body_statement':
                            body_node = child
                else:
                    # Generic fallback
                    for child in node.children:
                        if child.type in ['identifier', 'type_identifier', 'constant'] and name_node is None:
                            name_node = child
                        elif child.type in ['block', 'class_body', 'body']:
                            body_node = child
                
                class_name = self._get_node_text(name_node, code_text) if name_node else None
                
                if class_name:
                    classes.append({
                        'name': class_name,
                        'body': self._get_node_text(body_node, code_text) if body_node else self._get_node_text(node, code_text),
                        'start_line': node.start_point[0] + 1,
                        'end_line': node.end_point[0] + 1,
                    })
            
            # Recursively traverse children
            for child in node.children:
                traverse(child)
        
        try:
            traverse(tree.root_node)
        except Exception as e:
            logger.warning("Error extracting classes for %s: %s", language, e)
        
        return classes
    
    def _extract_imports(self, tree, code_text: str, language: str) -> List[Dict]:
        """
        Extract import statements from parsed AST using tree traversal.
        
        Args:
            tree: Tree-sitter tree
            code_text: Full source code text
            language: Programming language
            
        Returns:
            List of import dictionaries
        """
        imports = []
        
        # Language-specific node types for imports
        import_types = {
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
        
        node_types = import_types.get(language, ['import_statement', 'import_declaration', 'import', 'use_declaration'])
        
        def traverse(node):
            """Recursively traverse the tree to find imports."""
            if node.type in node_types:
                import_text = self._get_node_text(node, code_text).strip()
                imports.append({
                    'text': import_text,
                    'line': node.start_point[0] + 1,
                })
            
            # Recursively traverse children
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
        
        Args:
            file_path: Path to the file
            code_text: Content of the file
            language: Optional language override (if None, detected from extension)
            
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
        # Get language from file path if not provided
        if language is None:
            language = self._get_language(file_path)
        
        if language is None:
            # Try to detect from file extension as fallback
            ext = file_path.suffix.lstrip('.')
            language = ext if ext else "unknown"
            logger.debug("Unsupported language for file: %s, using: %s", file_path, language)
            return {
                "file_path": str(file_path),
                "language": language,
                "functions": [],
                "classes": [],
                "imports": [],
                "code_text": code_text
            }
        
        try:
            # Get parser and parse
            parser = self._get_parser(language)
            if parser is None:
                logger.warning("Parser not available for language: %s", language)
                return {
                    "file_path": str(file_path),
                    "language": language,
                    "functions": [],
                    "classes": [],
                    "imports": [],
                    "code_text": code_text
                }
            
            tree = parser.parse(bytes(code_text, "utf8"))
            
            # Extract structures
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
            return {
                "file_path": str(file_path),
                "language": language or "unknown",
                "functions": [],
                "classes": [],
                "imports": [],
                "code_text": code_text
            }

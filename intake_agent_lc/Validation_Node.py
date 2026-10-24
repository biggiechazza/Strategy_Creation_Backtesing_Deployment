import ast
from typing import Literal
from pydantic import BaseModel, Field


class ValidationResult(BaseModel):
    validation_status: Literal['passed', 'failed', 'blocked']

    checks: list[str] = Field(default_factory=list)

    checks_skipped: list[dict[str, str]] = Field(default_factory=list)

    validation_errors: list[dict[str, str | None]] = Field(default_factory=list)


# Validation Node, Formatting node outputs here if 'ready'
# No model call
def validation_node(state):

    validation_errors = []
    checks = []
    checks_skipped = []

# Get state info relevant to validation
    formatted_source = state.get('formatted_source')
    entrypoint_name = state.get('entrypoint_name')
    format_status = state.get('format_status')
    questions = state.get('questions')
    incompatibilities = state.get('incompatibilities')

    checks = ['formatted_source', 'entrypoint_name', 'format_status', 'questions', 'incompatibilities']

    # Check formatted source
    if not isinstance(formatted_source, str) or not formatted_source.strip():
        validation_errors.append({
            'code': 'INVALID_SOURCE',
            'explanation': 'Formatted source must be a nonempty string.',
            'location': None})

    # Check entrypoint name
    if not isinstance(entrypoint_name, str) or not entrypoint_name.strip():
        validation_errors.append({
            'code': 'INVALID_ENTRYPOINT',
            'explanation': 'Entrypoint name must be a nonempty string.',
            'location': None})

    # Check formatting readiness
    if format_status != 'ready_for_validation':
        validation_errors.append({
            'code': 'INVALID_FORMAT_STATUS',
            'explanation': 'Formatting is not ready for validation.',
            'location': None})

    # Check unresolved questions
    if not isinstance(questions, list) or questions:
        validation_errors.append({
            'code': 'UNRESOLVED_QUESTIONS',
            'explanation': 'Questions must be an empty list.',
            'location': None})

    # Check unresolved incompatibilities
    if not isinstance(incompatibilities, list) or incompatibilities:
        validation_errors.append({
            'code': 'UNRESOLVED_INCOMPATIBILITIES',
            'explanation': 'Incompatibilities must be an empty list.',
            'location': None})

# ------
    # Validate code without executing it
    parsed_tree = None
    if isinstance(formatted_source, str) and formatted_source.strip():
        try:
            parsed_tree = ast.parse(formatted_source)
            checks.append('ast_parse')

        except (SyntaxError, ValueError) as error:
            checks.append('ast_parse') 
            validation_errors.append({
                'code': 'SYNTAX_ERROR',
                'explanation': str(error),
                'location': f'Line {error.lineno}' if isinstance(error, SyntaxError) else None
            })
            checks_skipped.append({
                'check': 'compile',
                'reason': 'Source parsing failed.'})
        else:
            try:
                compile(formatted_source, '<formatted_strategy>', 'exec')

            except (SyntaxError, ValueError) as error:
                validation_errors.append({
                    'code': 'COMPILATION_ERROR',
                    'explanation': str(error),
                    'location': f'Line {error.lineno}' if isinstance(error, SyntaxError) else None})
                

            finally:
                checks.append('compile')

    else:
        checks_skipped.extend([
            {'check': 'ast_parse', 'reason': 'Invalid or empty source.'},
            {'check': 'compile', 'reason': 'Invalid or empty source.'}])


# ------
        # Check that the entrypoint class is defined at module scope
    if parsed_tree is None:
        checks_skipped.append({
            'check': 'entrypoint_class',
            'reason': 'Source parsing did not succeed.'})

    elif not isinstance(entrypoint_name, str) or not entrypoint_name.strip():
        checks_skipped.append({
            'check': 'entrypoint_class',
            'reason': 'Invalid entrypoint name.'})

    else:
        checks.append('entrypoint_class')

        class_found = any(
            isinstance(node, ast.ClassDef)
            and node.name == entrypoint_name
            for node in parsed_tree.body)

        if not class_found:
            validation_errors.append({
                'code': 'MISSING_ENTRYPOINT_CLASS',
                'explanation': 'No module-level class matches entrypoint_name.',
                'location': None})

# ------
    # Check the evaluate() method without executing code
    if parsed_tree is None or not isinstance(entrypoint_name, str) or not entrypoint_name.strip():
        checks_skipped.append({
            'check': 'evaluate_signature',
            'reason': 'Valid parsed source and entrypoint name required.'})

    else:
        entrypoint_class = next(
            (node for node in parsed_tree.body
             if isinstance(node, ast.ClassDef)
             and node.name == entrypoint_name),
            None)

        if entrypoint_class is None:
            checks_skipped.append({
                'check': 'evaluate_signature',
                'reason': 'Entrypoint class not found.'})

        else:
            evaluate_method = next(
                (node for node in entrypoint_class.body
                 if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                 and node.name == 'evaluate'), None)

            if evaluate_method is None:
                checks.append('evaluate_signature')
                validation_errors.append({'code': 'MISSING_EVALUATE','explanation': 'No directly declared evaluate() method found.' 'Runtime verification must check inherited or dynamic methods.', 
                                          'location': f'Line {entrypoint_class.lineno}'})

            else:
                checks.append('evaluate_signature')

                if isinstance(evaluate_method, ast.AsyncFunctionDef):
                    validation_errors.append({
                        'code': 'ASYNC_EVALUATE',
                        'explanation': 'evaluate() must be synchronous.',
                        'location': f'Line {evaluate_method.lineno}'})

                else:
                    positional_args = (evaluate_method.args.posonlyargs + evaluate_method.args.args)
                    required_positional = (len(positional_args) - len(evaluate_method.args.defaults))

                    # Instance binding supplies self; the engine supplies two market-data arguments.
                    accepts_engine_arguments = (required_positional <= 3
                        and (len(positional_args) >= 3 or evaluate_method.args.vararg is not None))

                    if (
                        not accepts_engine_arguments
                        or any(default is None for default in evaluate_method.args.kw_defaults)):
                        validation_errors.append({
                            'code': 'INVALID_EVALUATE_SIGNATURE',
                            'explanation': 'evaluate() must accept self and two positional market-data arguments without additional required keyword-only arguments.',
                            'location': f'Line {evaluate_method.lineno}'})

                        # -----
        # Screen for prohibited operations without executing code

    # If parsing failed, skip this check because there is no AST to inspect
    if parsed_tree is None:
        checks_skipped.append({
            'check': 'prohibited_operations_scan',
            'reason': 'Source parsing failed.'})

    else:
        # Record that the security scan is being performed
        checks.append('prohibited_operations_scan')

        # Modules that strategies are not permitted to import
        prohibited_modules = {
            'os', 'sys', 'subprocess', 'socket',
            'requests', 'httpx', 'urllib',
            'sqlite3', 'psycopg', 'psycopg2',
            'sqlalchemy', 'pathlib', 'importlib',
            'ctypes', 'builtins'}

        # Built-in functions that could access files or execute arbitrary code
        prohibited_functions = {
            'open', 'exec', 'eval', 'compile',
            '__import__', 'input'}

        # Methods associated with external data loading, file access, or databases
        prohibited_methods = {
            'read_csv', 'read_excel', 'read_parquet',
            'read_sql', 'read_json', 'loadtxt',
            'genfromtxt', 'to_sql', 'urlopen',
            'read_text', 'read_bytes',
            'write_text', 'write_bytes'}

        # Walk through every part of the strategy's syntax tree
        for node in ast.walk(parsed_tree):
            violation = None

            # Check imported modules against the prohibited module names
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                if isinstance(node, ast.Import):
                    modules = [alias.name for alias in node.names]
                else:
                    modules = [node.module or '']

                # Examine each import and identify restricted modules
                for module in modules:
                    if module.split('.')[0] in prohibited_modules:
                        violation = f'Prohibited import: {module}'
                    elif module == 'app' or (
                        module.startswith('app.')
                        and module != 'app.models'
                    ):
                        violation = f'Prohibited application access: {module}'

                # Prevent relative imports that could access other project files
                if isinstance(node, ast.ImportFrom) and node.level:
                    violation = 'Relative imports are not permitted.'

            # Check whether the strategy calls a prohibited function or method
            elif isinstance(node, ast.Call):

                # Check direct function calls such as open() or exec()
                if isinstance(node.func, ast.Name):
                    if node.func.id in prohibited_functions:
                        violation = f'Prohibited function: {node.func.id}'

                # Check method calls such as read_csv() or read_text()
                elif isinstance(node.func, ast.Attribute):
                    if node.func.attr in prohibited_methods:
                        violation = f'Prohibited operation: {node.func.attr}'

            # Save any detected violation with its explanation and line number
            if violation:
                validation_errors.append({
                    'code': 'PROHIBITED_OPERATION',
                    'explanation': violation,
                    'location': f'Line {node.lineno}'})
    # STEP 7: Validate strategy imports against the approved allowlist

    # If parsing failed, we cannot inspect the strategy's imports
    if parsed_tree is None:
        checks_skipped.append({
            'check': 'dependency_policy',
            'reason': 'Source parsing failed.'
        })

    else:
        # Record that dependency validation was performed
        checks.append('dependency_policy')

        # Approved standard-library modules and application exceptions
        allowed_modules = {
            'math',
            'statistics',
            'datetime',
            'collections',
            'collections.abc',
            'itertools',
            'functools',
            'decimal',
            'fractions',
            'typing',
            'enum',
            'operator',
            'app.models',
            '__future__'}

        # Examine every import statement in the generated strategy
        for node in ast.walk(parsed_tree):

            # Handle regular imports: import math, import os, etc.
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name not in allowed_modules:
                        validation_errors.append({
                            'code': 'UNAPPROVED_IMPORT',
                            'explanation': f'Import not permitted: {alias.name}',
                            'location': f'Line {node.lineno}'})

            # Handle from-import statements: from math import sqrt
            elif isinstance(node, ast.ImportFrom):

                # Relative imports are not permitted
                if node.level > 0:
                    validation_errors.append({
                        'code': 'UNAPPROVED_IMPORT',
                        'explanation': 'Relative imports are not permitted.',
                        'location': f'Line {node.lineno}'})

                # The imported module must appear in the allowlist
                elif node.module not in allowed_modules:
                    validation_errors.append({
                        'code': 'UNAPPROVED_IMPORT',
                        'explanation': f'Import not permitted: {node.module}',
                        'location': f'Line {node.lineno}'})

                # Prevent wildcard imports that obscure imported names
                elif any(alias.name == '*' for alias in node.names):
                    validation_errors.append({
                        'code': 'WILDCARD_IMPORT',
                        'explanation': 'Wildcard imports are not permitted.',
                        'location': f'Line {node.lineno}'})

        # STEP 8: Check whether the strategy constructor requires arguments

    # We need valid source and an entrypoint name before inspecting the class
    if parsed_tree is None or not isinstance(entrypoint_name, str) or not entrypoint_name.strip():
        checks_skipped.append({
            'check': 'constructor_signature_static',
            'reason': 'Valid source and entrypoint name required.'
        })

    else:
        # Find the entrypoint class in the generated source
        entrypoint_class = next(
            (node for node in parsed_tree.body
             if isinstance(node, ast.ClassDef)
             and node.name == entrypoint_name),
            None
        )

        if entrypoint_class is None:
            checks_skipped.append({
                'check': 'constructor_signature_static',
                'reason': 'Entrypoint class not found.'
            })

        # Inheritance, class decorators, and metaclasses may change construction
        elif entrypoint_class.bases or entrypoint_class.keywords or entrypoint_class.decorator_list:
            checks_skipped.append({
                'check': 'constructor_signature_static',
                'reason': 'Class construction may depend on inheritance, decorators, or metaclasses.'
            })

        else:
            # Locate explicitly declared constructors
            constructors = [
                node for node in entrypoint_class.body
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name == '__init__'
            ]

            # Check whether constructor behavior may have been modified
            custom_construction = any(
                isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name == '__new__'
                for node in entrypoint_class.body
            )

            # Handle constructor definitions we cannot reliably inspect
            if (
                custom_construction
                or len(constructors) > 1
                or (constructors and constructors[0].decorator_list)
            ):
                checks_skipped.append({
                    'check': 'constructor_signature_static',
                    'reason': 'Constructor requires additional verification.'
                })

            else:
                checks.append('constructor_signature_static')

                # No __init__ means the class normally inherits object.__init__
                if constructors:
                    constructor = constructors[0]

                    # Count positional parameters, including the instance parameter
                    positional = (
                        constructor.args.posonlyargs
                        + constructor.args.args
                    )

                    # Default values make trailing positional arguments optional
                    required_positional = (
                        len(positional) - len(constructor.args.defaults)
                    )

                    # Check for required keyword-only arguments
                    required_keywords = any(
                        default is None
                        for default in constructor.args.kw_defaults
                    )

                    # Identify constructor signatures that cannot work normally
                    # Reject constructors that cannot accept zero user arguments
                    if (
                        isinstance(constructor, ast.AsyncFunctionDef)
                        or (not positional and constructor.args.vararg is None)
                        or required_positional > 1
                        or required_keywords
                    ):
                        validation_errors.append({
                            'code': 'INVALID_CONSTRUCTOR',
                            'explanation': 'Strategy constructor has an invalid signature or requires arguments.',
                            'location': f'Line {constructor.lineno}'})
    
        # STEP 9: Check for unsupported engine behavior and invalid signals

    # Without a parsed AST, these checks cannot run
    if parsed_tree is None:
        checks_skipped.extend([
            {
                'check': 'unsupported_engine_operations',
                'reason': 'Source parsing failed.'
            },
            {
                'check': 'signal_returns_static',
                'reason': 'Source parsing failed.'
            }
        ])

    else:
        # PART 1: Detect direct use of unsupported engine operations
        checks.append('unsupported_engine_operations')

        # Order-related functions and methods unavailable to strategies
        prohibited_order_calls = {
            'submit_order',
            'place_order',
            'cancel_order',
            'market_order',
            'limit_order',
            'stop_order',
            'partial_exit',
            'reverse_position'
        }

        # Application objects that strategies must not access directly
        prohibited_access = {'engine', 'account', 'broker'}

        # Examine all operations in the generated source
        for node in ast.walk(parsed_tree):

            # Look for calls to unsupported order functions or methods
            if isinstance(node, ast.Call):
                if isinstance(node.func, ast.Attribute):
                    function_name = node.func.attr
                elif isinstance(node.func, ast.Name):
                    function_name = node.func.id
                else:
                    function_name = None

                if function_name in prohibited_order_calls:
                    validation_errors.append({
                        'code': 'UNSUPPORTED_ORDER_OPERATION',
                        'explanation': f'Unsupported order operation: {function_name}',
                        'location': f'Line {node.lineno}'
                    })

            # Detect direct references to restricted application objects
            if isinstance(node, ast.Attribute):
                if (
                    isinstance(node.value, ast.Name)
                    and node.value.id == 'self'
                    and node.attr in prohibited_access
                ):
                    validation_errors.append({
                        'code': 'RESTRICTED_ENGINE_ACCESS',
                        'explanation': f'Direct access to self.{node.attr} is not permitted.',
                        'location': f'Line {node.lineno}'
                    })

        # PART 2: Inspect explicit return statements in evaluate()

        # Find the declared entrypoint class
        entrypoint_class = next(
            (node for node in parsed_tree.body
             if isinstance(node, ast.ClassDef)
             and node.name == entrypoint_name),
            None
        )

        # Find evaluate() inside that class
        evaluate_method = None

        if entrypoint_class is not None:
            evaluate_method = next(
                (node for node in entrypoint_class.body
                 if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                 and node.name == 'evaluate'),
                None
            )

        # Skip the return check if the method cannot be inspected
        if evaluate_method is None:
            checks_skipped.append({
                'check': 'signal_returns_static',
                'reason': 'A directly declared evaluate() method was not found.'
            })

        else:
            checks.append('signal_returns_static')

            # Only these four Signal members are supported by the engine
            allowed_signals = {'BUY', 'SELL', 'EXIT', 'HOLD'}

            # Examine statements in evaluate(), including conditional branches
            pending = list(evaluate_method.body)

            while pending:
                current = pending.pop()

                # Ignore nested functions because their returns belong elsewhere
                if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef,ast.ClassDef, ast.Lambda)):
                    continue

                if isinstance(current, ast.Return):
                    value = current.value

                    # Explicit literal returns cannot be application Signal objects
                    if value is None or isinstance(value, ast.Constant):
                        validation_errors.append({
                            'code': 'INVALID_SIGNAL_RETURN',
                            'explanation': 'evaluate() returns a literal or None instead of an application Signal.','location': f'Line {current.lineno}'})

                    # Detect references to unsupported Signal members
                    elif (
                        isinstance(value, ast.Attribute)
                        and isinstance(value.value, ast.Name)
                        and value.value.id == 'Signal'
                        and value.attr not in allowed_signals):
                        validation_errors.append({
                            'code': 'INVALID_SIGNAL_MEMBER',
                            'explanation': f'Unsupported Signal member: {value.attr}',
                            'location': f'Line {current.lineno}'})

                # Continue inspecting nested statements and branches
                pending.extend(ast.iter_child_nodes(current))

        # STEP 10: Determine validation outcome and return findings

    # A detected defect takes priority over checks that were skipped
    if validation_errors:
        validation_status = 'failed'

    # No detected defects, but required checks remain incomplete
    elif checks_skipped:
        validation_status = 'blocked'

    # Every required check must be completed successfully to pass
    else:
        validation_status = 'passed'

    # Package all findings using the existing Pydantic schema
    result = ValidationResult(validation_status=validation_status,validation_errors=validation_errors,checks=checks,checks_skipped=checks_skipped)

    # Return the structured results to LangGraph state
    return result.model_dump()

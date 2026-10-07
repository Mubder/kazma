"""A deliberately small SQL read grammar, compiled rather than forwarded raw."""
from __future__ import annotations

MAX_ROWS = 1000
MAX_QUERY_CHARS = 16384
MAX_OUTPUT_CHARS = 1_000_000

# Exact node classes: new syntax/functions fail closed on parser upgrades.
# No casts, user operators, qualified functions, windows, INTO, locks,
# table functions, hints, recursion or executable SQL hidden inside a CTE.
_READ_NODES = frozenset({
    "Select", "Column", "Identifier", "Table", "From", "Where", "Order", "Ordered",
    "Group", "Having", "Limit", "Offset", "Alias", "TableAlias", "Star", "Literal",
    "Placeholder", "Paren", "And", "Or", "Not", "EQ", "NEQ", "GT", "GTE", "LT",
    "LTE", "Is", "Between", "In", "Null", "Boolean", "Distinct", "Join", "Subquery",
    "With", "CTE", "Count", "Sum", "Avg", "Min", "Max", "Lower", "Upper", "Length",
    "Abs", "Round", "Coalesce", "Nullif",
})


def bounded_rows(limit: int) -> int:
    """Refuse invalid limits; a negative driver fetchmany can fetch everything."""
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_ROWS:
        raise ValueError(f"limit must be an integer from 1 to {MAX_ROWS}")
    return limit


def compile_read(query: str, *, dialect: str, tables: frozenset[str], limit: int) -> tuple[str, frozenset[str]]:
    """Compile a single supported SELECT over declared tables and CTEs."""
    bounded_rows(limit)
    if not isinstance(query, str) or not query.strip() or len(query) > MAX_QUERY_CHARS:
        raise ValueError("SQL query is empty or exceeds the query size limit")
    try:
        import sqlglot
        from sqlglot import exp
    except ImportError as exc:
        raise ValueError("Remote SQL reads need the database extra (sqlglot)") from exc
    try:
        if dialect == "mysql":
            # PyMySQL's %s is not a MySQL SQL token. Normalize only unquoted
            # token pairs, then emit bindings from AST Placeholder nodes.
            tokens = sqlglot.tokenize(query, read="mysql")
            replacements = [(a.start, b.end + 1) for a, b in zip(tokens, tokens[1:])
                            if a.token_type == sqlglot.tokens.TokenType.MOD
                            and b.token_type == sqlglot.tokens.TokenType.VAR
                            and b.text == "s" and a.end + 1 == b.start]
            for start, end in reversed(replacements):
                query = query[:start] + "?" + query[end:]
        statements = sqlglot.parse(query, read=dialect)
    except sqlglot.errors.ParseError as exc:
        raise ValueError("Unsupported or invalid SQL syntax") from exc
    if len(statements) != 1 or not isinstance(statements[0], exp.Select):
        raise ValueError("Only one SELECT query is supported")
    tree = statements[0]
    if sum(1 for _ in tree.walk()) > 512:
        raise ValueError("SQL exceeds the syntax complexity limit")
    if any(type(node).__name__ not in _READ_NODES for node in tree.walk()):
        raise ValueError("SQL contains an unsupported operation or function")
    if any(node.args.get("recursive") for node in tree.walk()):
        raise ValueError("Recursive SQL is not supported")
    if any(node.this for node in tree.find_all(exp.Placeholder)):
        raise ValueError("Use positional SQL parameters")
    from sqlglot.optimizer.scope import Scope, traverse_scope

    used = set()
    for scope in traverse_scope(tree):
        for table in scope.tables:
            if table.catalog:
                raise ValueError("Cross-database SQL is not supported")
            if isinstance(scope.sources.get(table.alias_or_name), Scope):
                continue
            name = f"{table.db}.{table.name}"
            if name not in tables:
                raise ValueError("SQL table is outside this connection's capability")
            used.add(name)
    if len(used) > 8:
        raise ValueError("SQL may read at most eight declared tables")
    # The parsed tree is the payload; comments/hints/extraneous raw text never
    # reach the server. The outer bound applies even to SQL without LIMIT.
    # Both DB-API drivers scan percent signs even inside SQL literals and
    # quoted identifiers. Escape data tokens, leaving actual bindings intact.
    base_dialect = sqlglot.Dialect.get_or_raise(dialect).__class__

    class DriverBindings(base_dialect):
        class Generator(base_dialect.Generator):
            TRANSFORMS = {**base_dialect.Generator.TRANSFORMS,
                          exp.Placeholder: lambda generator, expression: "%s"}

            def literal_sql(self, expression):
                return super().literal_sql(expression).replace("%", "%%")

            def identifier_sql(self, expression):
                return super().identifier_sql(expression).replace("%", "%%")

    output_dialect = DriverBindings
    sql = tree.sql(dialect=output_dialect, comments=False, unsupported_level=sqlglot.ErrorLevel.RAISE)
    return f'SELECT * FROM ({sql}) AS kazma_read_result LIMIT {limit}', frozenset(used)


def validate_mongo_filter(query: str) -> dict:
    """Only data predicates, never JavaScript/$where/$function expressions."""
    import json

    if len(query) > MAX_QUERY_CHARS:
        raise ValueError("MongoDB filter exceeds the query size limit")
    document = json.loads(query or "{}")
    if not isinstance(document, dict):
        raise ValueError("MongoDB filter must be an object")
    allowed = {"$eq", "$ne", "$gt", "$gte", "$lt", "$lte", "$in", "$nin",
               "$and", "$or", "$not", "$nor", "$exists"}

    def walk(value, depth=0):
        if depth > 20:
            raise ValueError("MongoDB filter exceeds the nesting limit")
        if isinstance(value, dict):
            for key, item in value.items():
                if key.startswith("$") and key not in allowed:
                    raise ValueError("MongoDB filter contains an unsupported operator")
                walk(item, depth + 1)
        elif isinstance(value, list):
            for item in value:
                walk(item, depth + 1)

    walk(document)
    return document

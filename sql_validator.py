"""
SQL Validator Module
===================
Validates SQL queries for safety and correctness
"""

import re

def validate_sql(sql: str, allow_dml: bool = False):
    """
    Validate SQL query for safety
    
    Args:
        sql: SQL query to validate
        allow_dml: Whether to allow INSERT/UPDATE/DELETE
        
    Raises:
        ValueError: If SQL is invalid or unsafe
    """
    sql_upper = sql.upper()
    
    # Check for dangerous operations
    dangerous_keywords = ['DROP', 'TRUNCATE', 'ALTER', 'CREATE']
    for keyword in dangerous_keywords:
        if keyword in sql_upper:
            raise ValueError(f"Dangerous operation detected: {keyword}")
    
    # Check DML if not allowed
    if not allow_dml:
        dml_keywords = ['INSERT', 'UPDATE', 'DELETE']
        for keyword in dml_keywords:
            if sql_upper.startswith(keyword):
                raise ValueError(f"DML operation not allowed: {keyword}")
    
    # Check for UPDATE/DELETE without WHERE
    if 'UPDATE' in sql_upper and 'WHERE' not in sql_upper:
        raise ValueError("UPDATE without WHERE clause is not allowed")
    
    if 'DELETE' in sql_upper and 'WHERE' not in sql_upper:
        raise ValueError("DELETE without WHERE clause is not allowed")
    
    # Basic SQL injection checks
    if '--' in sql or ';' in sql[:-1]:
        raise ValueError("Potential SQL injection detected")
    
    return True


def analyze_query_complexity(sql: str) -> dict:
    """
    Analyze query complexity
    
    Args:
        sql: SQL query
        
    Returns:
        Dictionary with complexity metrics
    """
    sql_upper = sql.upper()
    
    return {
        'has_join': 'JOIN' in sql_upper,
        'has_subquery': '(' in sql and 'SELECT' in sql_upper,
        'has_cte': 'WITH' in sql_upper,
        'has_window_function': 'OVER' in sql_upper,
        'has_aggregation': any(word in sql_upper for word in ['COUNT', 'SUM', 'AVG', 'MAX', 'MIN']),
        'has_group_by': 'GROUP BY' in sql_upper,
        'has_order_by': 'ORDER BY' in sql_upper
    }

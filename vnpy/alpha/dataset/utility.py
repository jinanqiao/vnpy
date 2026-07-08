import ast
from datetime import datetime
from enum import Enum
from numbers import Real
from typing import Union, cast
from collections.abc import Callable

import polars as pl


EXPRESSION_FUNCTIONS: dict[str, Callable] = {}


class ExpressionValidator(ast.NodeVisitor):
    """Validate factor expressions before evaluation."""

    allowed_bin_ops: tuple[type[ast.operator], ...] = (
        ast.Add,
        ast.Sub,
        ast.Mult,
        ast.Div,
        ast.FloorDiv,
        ast.Mod,
        ast.Pow,
    )
    allowed_unary_ops: tuple[type[ast.unaryop], ...] = (ast.UAdd, ast.USub)
    allowed_compare_ops: tuple[type[ast.cmpop], ...] = (
        ast.Eq,
        ast.NotEq,
        ast.Lt,
        ast.LtE,
        ast.Gt,
        ast.GtE,
    )

    def __init__(self, allowed_names: set[str]) -> None:
        self.allowed_names = allowed_names

    def generic_visit(self, node: ast.AST) -> None:
        allowed_nodes: tuple[type[ast.AST], ...] = (
            ast.Expression,
            ast.BinOp,
            ast.UnaryOp,
            ast.Compare,
            ast.Call,
            ast.Name,
            ast.Load,
            ast.Constant,
        )
        if not isinstance(node, allowed_nodes):
            raise ValueError(f"Unsupported expression syntax: {type(node).__name__}")
        super().generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        if node.id not in self.allowed_names:
            raise ValueError(f"Unknown expression name: {node.id}")

    def visit_BinOp(self, node: ast.BinOp) -> None:
        if not isinstance(node.op, self.allowed_bin_ops):
            raise ValueError(f"Unsupported expression operator: {type(node.op).__name__}")
        self.visit(node.left)
        self.visit(node.right)

    def visit_UnaryOp(self, node: ast.UnaryOp) -> None:
        if not isinstance(node.op, self.allowed_unary_ops):
            raise ValueError(f"Unsupported expression operator: {type(node.op).__name__}")
        self.visit(node.operand)

    def visit_Compare(self, node: ast.Compare) -> None:
        for operator in node.ops:
            if not isinstance(operator, self.allowed_compare_ops):
                raise ValueError(f"Unsupported expression operator: {type(operator).__name__}")
        self.visit(node.left)
        for comparator in node.comparators:
            self.visit(comparator)

    def visit_Call(self, node: ast.Call) -> None:
        if not isinstance(node.func, ast.Name):
            raise ValueError(f"Unsupported expression syntax: {type(node.func).__name__}")
        if node.func.id not in self.allowed_names:
            raise ValueError(f"Unknown expression name: {node.func.id}")
        if node.keywords:
            raise ValueError("Unsupported expression syntax: keyword arguments")
        for arg in node.args:
            self.visit(arg)


def register_functions(functions: list[Callable]) -> None:
    """Register custom expression functions by function name."""
    for func in functions:
        EXPRESSION_FUNCTIONS[func.__name__] = func


def validate_expression(expression: str, allowed_names: set[str]) -> None:
    """Validate expression syntax and referenced names."""
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as exc:
        raise ValueError(f"Invalid expression syntax: {exc.msg}") from exc

    ExpressionValidator(allowed_names).visit(tree)


def cast_to_int(feature: "DataProxy") -> "DataProxy":
    """Cast expression result data to Int32."""
    return DataProxy(feature.df.with_columns(pl.col("data").cast(pl.Int32)))


class DataProxy:
    """Feature data proxy"""

    def __init__(self, df: pl.DataFrame) -> None:
        """Constructor"""
        self.name: str = df.columns[-1]
        self.df: pl.DataFrame = df.rename({self.name: "data"})

        # Note that for numerical expressions, variables should be placed before numbers. e.g. a * 2
    @staticmethod
    def _as_series(value: object) -> pl.Series:
        """Normalize an operator result to a Polars series."""
        if isinstance(value, pl.Series):
            return value

        return cast(pl.Series, value)

    def _comparison_series(self, value: object) -> pl.Series:
        """Normalize comparison results to an Int32 series."""
        if isinstance(value, pl.Series):
            return value.cast(pl.Int32)

        if isinstance(value, bool):
            return pl.Series(name="data", values=[int(value)] * len(self.df))

        if isinstance(value, Real):
            return pl.Series(name="data", values=[int(bool(value))] * len(self.df))

        raise TypeError(f"Unsupported comparison result type: {type(value)!r}")

    def result(self, s: pl.Series) -> "DataProxy":
        """Convert series data to feature object"""
        result: pl.DataFrame = self.df[["datetime", "vt_symbol"]]
        result = result.with_columns(other=s)

        return DataProxy(result)

    def __add__(self, other: Union["DataProxy", Real]) -> "DataProxy":
        """Addition operation"""
        if isinstance(other, DataProxy):
            s = self._as_series(self.df["data"] + other.df["data"])
        else:
            s = self._as_series(self.df["data"] + other)
        return self.result(s)

    def __radd__(self, other: Union["DataProxy", Real]) -> "DataProxy":
        """Right addition operation"""
        if isinstance(other, DataProxy):
            s = self._as_series(other.df["data"] + self.df["data"])
        else:
            s = self._as_series(other + self.df["data"])
        return self.result(s)

    def __sub__(self, other: Union["DataProxy", Real]) -> "DataProxy":
        """Subtraction operation"""
        if isinstance(other, DataProxy):
            s = self._as_series(self.df["data"] - other.df["data"])
        else:
            s = self._as_series(self.df["data"] - other)
        return self.result(s)

    def __rsub__(self, other: Union["DataProxy", Real]) -> "DataProxy":
        """Right subtraction operation"""
        if isinstance(other, DataProxy):
            s = self._as_series(other.df["data"] - self.df["data"])
        else:
            s = self._as_series(other - self.df["data"])
        return self.result(s)

    def __mul__(self, other: Union["DataProxy", Real]) -> "DataProxy":
        """Multiplication operation"""
        if isinstance(other, DataProxy):
            s = self._as_series(self.df["data"] * other.df["data"])
        else:
            s = self._as_series(self.df["data"] * other)
        return self.result(s)

    def __rmul__(self, other: Union["DataProxy", Real]) -> "DataProxy":
        """Right multiplication operation"""
        if isinstance(other, DataProxy):
            s = self._as_series(self.df["data"]  * other.df["data"])
        else:
            s = self._as_series(self.df["data"] * other)
        return self.result(s)

    def __truediv__(self, other: Union["DataProxy", Real]) -> "DataProxy":
        """Division operation"""
        if isinstance(other, DataProxy):
            s = self._as_series(self.df["data"] / other.df["data"])
        else:
            s = self._as_series(self.df["data"] / other)
        return self.result(s)

    def __rtruediv__(self, other: Union["DataProxy", Real]) -> "DataProxy":
        """Right division operation"""
        if isinstance(other, DataProxy):
            s = self._as_series(other.df["data"] / self.df["data"])
        else:
            s = self._as_series(other / self.df["data"])
        return self.result(s)

    def __floordiv__(self, other: Union["DataProxy", Real]) -> "DataProxy":
        """Floor division operation"""
        if isinstance(other, DataProxy):
            s = self._as_series(self.df["data"] // other.df["data"])
        else:
            s = self._as_series(self.df["data"] // other)
        return self.result(s)

    def __mod__(self, other: Union["DataProxy", Real]) -> "DataProxy":
        """Modulo operation"""
        if isinstance(other, DataProxy):
            s = self._as_series(self.df["data"] % other.df["data"])
        else:
            s = self._as_series(self.df["data"] % other)
        return self.result(s)

    def __pow__(self, other: Union["DataProxy", Real]) -> "DataProxy":
        """Power operation"""
        if isinstance(other, DataProxy):
            s = self._as_series(self.df["data"].pow(other.df["data"]))
        else:
            s = self._as_series(self.df["data"].pow(cast(int | float, other)))
        return self.result(s)

    def __abs__(self) -> "DataProxy":
        """Get absolute value"""
        s: pl.Series = self.df["data"].abs()
        return self.result(s)

    def __neg__(self) -> "DataProxy":
        """Negation operation"""
        s: pl.Series = -self.df["data"]
        return self.result(s)

    def __gt__(self, other: Union["DataProxy", Real]) -> "DataProxy":
        """Greater than comparison"""
        if isinstance(other, DataProxy):
            s: object = self.df["data"] > other.df["data"]
        else:
            s = self.df["data"] > other
        return self.result(self._comparison_series(s))

    def __ge__(self, other: Union["DataProxy", Real]) -> "DataProxy":
        """Greater than or equal comparison"""
        if isinstance(other, DataProxy):
            s: object = self.df["data"] >= other.df["data"]
        else:
            s = self.df["data"] >= other
        return self.result(self._comparison_series(s))

    def __lt__(self, other: Union["DataProxy", Real]) -> "DataProxy":
        """Less than comparison"""
        if isinstance(other, DataProxy):
            s: object = self.df["data"] < other.df["data"]
        else:
            s = self.df["data"] < other
        return self.result(self._comparison_series(s))

    def __le__(self, other: Union["DataProxy", Real]) -> "DataProxy":
        """Less than or equal comparison"""
        if isinstance(other, DataProxy):
            s: object = self.df["data"] <= other.df["data"]
        else:
            s = self.df["data"] <= other
        return self.result(self._comparison_series(s))

    def __eq__(self, other: Union["DataProxy", Real]) -> "DataProxy":  # type: ignore[override]
        """Equal comparison"""
        if isinstance(other, DataProxy):
            s: object = self.df["data"] == other.df["data"]
        else:
            s = self.df["data"] == other
        return self.result(self._comparison_series(s))

    def __ne__(self, other: Union["DataProxy", Real]) -> "DataProxy":  # type: ignore[override]
        """Not equal comparison"""
        if isinstance(other, DataProxy):
            s: object = self.df["data"] != other.df["data"]
        else:
            s = self.df["data"] != other
        return self.result(self._comparison_series(s))


def calculate_by_expression(df: pl.DataFrame, expression: str) -> pl.DataFrame:
    """Execute calculation based on expression"""
    # Import operators locally to avoid polluting global namespace
    from .ts_function import (              # noqa
        ts_delay,
        ts_min, ts_max,
        ts_argmax, ts_argmin,
        ts_rank, ts_sum,
        ts_mean, ts_std,
        ts_slope, ts_quantile,
        ts_rsquare, ts_resi,
        ts_corr,
        ts_less, ts_greater,
        ts_log, ts_abs,
        ts_delta, ts_cov,
        ts_decay_linear,
        ts_product
    )
    from .cs_function import (              # noqa
        cs_rank,
        cs_mean,
        cs_std,
        cs_sum,
        cs_scale
    )
    from .ta_function import (              # noqa
        ta_rsi,
        ta_atr
    )
    from .math_function import (              # noqa
        less, greater, log, abs,
        sign, pow1, pow2,
        quesval, quesval2
    )

    # Extract feature objects to local space
    d: dict = locals()
    d.update(EXPRESSION_FUNCTIONS)
    d["cast_to_int"] = cast_to_int

    for column in df.columns:
        # Filter index columns
        if column in {"datetime", "vt_symbol"}:
            continue

        # Cache feature df
        column_df = df[["datetime", "vt_symbol", column]]
        d[column] = DataProxy(column_df)

    validate_expression(expression, set(d))

    # Use eval with a validated expression and no builtins.
    other: DataProxy = eval(expression, {}, d)

    # Return result DataFrame
    return other.df


def calculate_by_polars(df: pl.DataFrame, expression: pl.expr.expr.Expr) -> pl.DataFrame:
    """Execute calculation based on Polars expression"""
    return df.select([
        "datetime",
        "vt_symbol",
        expression.alias("data")
    ])


def to_datetime(arg: datetime | str) -> datetime:
    """Convert time data type"""
    if isinstance(arg, str):
        if "-" in arg:
            fmt: str = "%Y-%m-%d"
        else:
            fmt = "%Y%m%d"

        return datetime.strptime(arg, fmt)
    else:
        return arg


class Segment(Enum):
    """Data segment enumeration values"""

    TRAIN = 1
    VALID = 2
    TEST = 3

# pylint: disable=missing-function-docstring,missing-module-docstring,wildcard-import,undefined-variable,function-redefined,no-else-return
import random
import re
import string
import time

import inflect
from behave import *
from pyspark.sql import types as T


def string_to_type(s):
    tname = s.lower()
    if tname == "int":
        return T.IntegerType()
    elif tname in ["long", "timestamp"]:
        return T.LongType()
    elif tname == "double":
        return T.DoubleType()
    else:
        return T.StringType()


def random_cell(field_type, mode):
    r = re.compile(r"^.*RAND(\(([0-9]+)-([0-9]+)\))?.*$")

    (_, l, u) = r.match(mode).groups()

    lower = int(l) if l else 0
    upper = int(u) if u else 2147483647

    t = field_type.lower()
    if t in ["int", "long", "timestamp"]:
        return random.randint(lower, upper)
    elif t == "double":
        return random.random()
    else:
        return "".join(random.choices(string.ascii_lowercase, k=24))


def seq_cell(sequence_positions, inflect_engine, name, field_type):
    val = sequence_positions.get(name, 0) + 1
    sequence_positions[name] = val

    dt = field_type.lower()
    if dt in ["int", "long", "timestamp"]:
        return val
    if dt == "double":
        return float(val)
    elif dt in ["string", "str"]:
        return inflect_engine.number_to_words(val)
    else:
        raise ValueError("Data type not supported for sequences {0}".format(dt))


def auto_row(sequence_positions, inflect_engine, cols):
    for (name, ftype, mode) in cols:
        if "RAND" in mode:
            yield random_cell(ftype, mode)
        elif mode == "SEQ":
            yield seq_cell(sequence_positions, inflect_engine, name, ftype)


def parse_ts(s):
    t = time.mktime(time.strptime(s, "%Y-%m-%d %H:%M:%S"))
    return int(t)


def process_cells(cols, cells):
    data = list(zip(cols, cells))
    for ((_, ftype), cell) in data:

        if "%RAND" in cell:
            yield random_cell(ftype, cell)
        elif ftype == "timestamp":
            yield parse_ts(cell)
        else:
            yield cell


def table_to_spark(spark, table):
    cols = [h.split(":") for h in table.headings]

    if len([c for c in cols if len(c) != 2]) > 0:
        raise ValueError("You must specify name AND data type for columns like this 'my_field:string'")

    schema = T.StructType([T.StructField(name + "_str", T.StringType(), False) for (name, _) in cols])
    rows = [list(process_cells(cols, row.cells)) for row in table]
    df = spark.createDataFrame(rows, schema=schema)

    for (name, field_type) in cols:
        df = df.withColumn(name, df[name + "_str"].cast(string_to_type(field_type))).drop(name + "_str")

    return df


def generate_random_table(spark, config, row_count):
    sequence_positions = {}
    inflect_engine = inflect.engine()
    cols = [(row["name"], row["type"], row["mode"]) for row in config]
    schema = T.StructType([T.StructField(name, string_to_type(ftype), False) for (name, ftype, _) in cols])
    rows = [list(auto_row(sequence_positions, inflect_engine, cols)) for _ in range(0, int(row_count))]
    df = spark.createDataFrame(rows, schema=schema)
    return df


@given(u'a table called "{table_name}" containing')
def step_impl(context, table_name):
    df = table_to_spark(context.spark, context.table)
    df.createOrReplaceTempView(table_name)


@given(u'a table called "{table_name}" containing "{row_count}" rows with schema')
def step_impl(context, table_name, row_count):
    df = generate_random_table(context.spark, context.table, row_count)
    df.createOrReplaceTempView(table_name)


@then(u'the table "{table_name}" contains')
def step_impl(context, table_name):
    expected_df = table_to_spark(context.spark, context.table)
    actual_df = context.spark.sql("select * from {0}".format(table_name))

    # print("\n\n\nEXPECTED:")
    # expected_df.show()
    # print("ACTUAL:")
    # actual_df.show()

    assert expected_df.schema == actual_df.schema
    assert expected_df.subtract(actual_df).count() == 0
    assert actual_df.subtract(expected_df).count() == 0

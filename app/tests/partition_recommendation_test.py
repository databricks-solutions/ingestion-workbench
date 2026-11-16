"""A job made from a discovery (New ingestion job, Add tables, re-discovery) loads a large table partitioned, as Bulk
import's review recommends: a table of at least queries.DEFAULT_PARTITION_THRESHOLD_MB with a partition candidate
gets load_partitioned on that column in partitions of DEFAULT_PARTITION_SIZE_MB. The rows reach new_job.plan as the
discovery CSV (DISCOVERY_COLUMNS): queries.source_rows_sql gives each table's size and partition candidate
(partition_recommendation_pg_test), and new_job.csv_of writes the partition columns queries.recommended_partitioning
decides. A CSV a user gives (pasted or uploaded) sets the partition columns itself, checked strictly."""
import new_job as nj
import queries as q

PLAIN = {"sink_catalog": "main", "sink_schema": "s", "sink_table": "{database}_{table}"}


def _discovered(table, large=False, column="id"):
    """A discovered table's fields as source_rows_sql gives them: a large one at the threshold, a small one just under."""
    size = q.DEFAULT_PARTITION_THRESHOLD_MB if large else q.DEFAULT_PARTITION_THRESHOLD_MB - 1
    return {"src_connection": "sql_a", "src_database": "sales", "src_schema": "dbo", "src_table": table,
            "select_list": "id, amount", "primary_key": "id", "load_type": "full", "size_mb": f"{size}.00",
            "partition_candidate": column}


def _rows(plan):
    return {line["row"]["src_table"]: (line["status"], line["row"].get("load_partitioned"), line["row"].get("partition_col"),
                                       line["row"].get("partition_size_mb")) for line in plan["lines"]}


def test_a_discovered_large_table_is_planned_partitioned_and_the_others_are_not():
    text = nj.csv_of(list(nj.DISCOVERY_COLUMNS), [_discovered("big", True, "order_id"), _discovered("small")])
    plan = nj.plan("Sales", text, PLAIN, 1)
    assert plan["errors"] == []
    assert _rows(plan) == {"big": ("valid", True, "order_id", q.DEFAULT_PARTITION_SIZE_MB),
                           "small": ("valid", None, None, None)}


def test_a_plain_csv_without_the_partition_columns_plans_no_partitioning():
    text = nj.csv_of(["src_connection", "src_database", "src_schema", "src_table", "select_list", "primary_key"],
                     [_discovered("t1")])
    assert _rows(nj.plan("Sales", text, PLAIN, 1)) == {"t1": ("valid", None, None, None)}


def test_re_discovery_and_add_tables_plan_the_same_partitioning_into_an_existing_job():
    job_rows = [{"id": 1, "task_collection": "sales_c01", "src_type": "sqlserver", "src_connection": "sql_a",
                 "use_remote_query": True, "src_database": "sales", "src_schema": "dbo", "src_table": "old",
                 "select_list": "id", "primary_key": "id", "load_type": "full", "write_mode": "merge",
                 "sink_catalog": "main", "sink_schema": "s", "sink_table": "sales_old", "client_id": None,
                 "client_attrs": None, "freshness_sla_minutes": None, "jdbc_options": None}]
    text = nj.csv_of(list(nj.DISCOVERY_COLUMNS), [_discovered("big", True), _discovered("small")])
    plan = nj.plan_for_job("Sales", text, "full", job_rows)
    rows = _rows(plan)
    assert rows["big"][1:] == (True, "id", q.DEFAULT_PARTITION_SIZE_MB) and rows["small"][1:] == (None, None, None)


def test_a_large_discovered_table_without_a_partition_candidate_or_a_size_is_not_partitioned():
    no_column = dict(_discovered("big_no_column", True), partition_candidate=None)
    no_size = dict(_discovered("unknown_size", True), size_mb=None)
    plan = nj.plan("Sales", nj.csv_of(list(nj.DISCOVERY_COLUMNS), [no_column, no_size]), PLAIN, 1)
    assert _rows(plan) == {"big_no_column": ("valid", None, None, None), "unknown_size": ("valid", None, None, None)}


def test_a_line_marked_partitioned_without_a_column_is_refused_by_the_row_check():
    columns = list(nj.DISCOVERY_COLUMNS)
    broken = dict(_discovered("big", True), load_partitioned="true", partition_col="", partition_size_mb="2048")
    plan = nj.plan("Sales", nj.csv_of(columns, [broken]), PLAIN, 1)
    [line] = plan["lines"]
    assert line["status"] == "invalid" and "partitioned loads need partition_col and partition_size_mb" in line["errors"]


def test_line_columns_numbers_lines_as_the_import_does_and_keeps_extra_cells():
    text = "a,load_type,partition_col,b\n1,ct,id,2\n,,,\n3,,,4,extra\n"
    plain, values = nj._line_columns(text, ("load_type", "partition_col"))
    assert plain == "a,b\n1,2\n,\n3,4,extra\n"           # the blank line stays blank; the extra cell stays
    assert values == {2: {"load_type": "ct", "partition_col": "id"}}   # line 3 (the third with a value) has none
    assert nj._line_load_types(text)[1] == {2: "ct"}


def test_the_defaults_partition_only_large_tables_in_large_partitions():
    assert q.DEFAULT_PARTITION_THRESHOLD_MB == 10240 and q.DEFAULT_PARTITION_SIZE_MB == 2048
    assert q.recommended_partitioning(10240, "id", q.DEFAULT_PARTITION_THRESHOLD_MB, q.DEFAULT_PARTITION_SIZE_MB) == (
        True, "id", 2048)
    assert q.recommended_partitioning(5000, "id", q.DEFAULT_PARTITION_THRESHOLD_MB, q.DEFAULT_PARTITION_SIZE_MB)[0] is False


def test_a_tables_csv_upload_takes_the_partition_columns_per_line():
    text = ("src_connection,src_database,src_schema,src_table,select_list,primary_key,load_partitioned,partition_col,"
            "partition_size_mb\n"
            'sql_a,sales,dbo,big,"id, amount",id,true,id,4096\n'
            'sql_a,sales,dbo,small,"id, amount",id,,,\n')
    read = nj.read_upload(text, "sql_a")
    assert read["errors"] == []
    fields = {line["src_table"]: line["fields"] for line in read["lines"]}
    assert (fields["big"]["load_partitioned"], fields["big"]["partition_col"], fields["big"]["partition_size_mb"]) == (
        "true", "id", "4096")
    plan = nj.plan("Sales", nj.csv_of(read["columns"], [line["fields"] for line in read["lines"]]), PLAIN, 1)
    assert _rows(plan) == {"big": ("valid", True, "id", 4096), "small": ("valid", None, None, None)}


def test_a_line_that_gives_the_partition_columns_is_taken_as_written_over_the_recommendation():
    columns = list(nj.DISCOVERY_COLUMNS)
    kept_whole = dict(_discovered("big", True), load_partitioned="false")
    plan = nj.plan("Sales", nj.csv_of(columns, [kept_whole]), PLAIN, 1)
    assert _rows(plan) == {"big": ("valid", None, None, None)}


USER_HEADER = "src_connection,src_database,src_schema,src_table,select_list,primary_key"


def _user_plan(extra_header: str, extra_cells: str):
    return nj.plan("Sales", f"{USER_HEADER},{extra_header}\nsql_a,sales,dbo,t,\"id, amount\",id,{extra_cells}\n", PLAIN, 1)


def test_a_csv_a_user_gives_cant_carry_a_size_or_candidate():
    # Only a discovered row's fields carry them (csv_of turns them into the partition columns); in a user's CSV they
    # are unknown columns, as before, never read (a bad size once crashed the plan).
    for cells in ("big,id", "99999,id"):
        plan = _user_plan("size_mb,partition_candidate", cells)
        assert plan["errors"] == ["unknown column size_mb", "unknown column partition_candidate"]


def test_csv_of_writes_a_discovered_tables_partition_columns_and_leaves_an_uploads_as_written():
    big = nj.plan_fields(_discovered("big", True, "order_id"))
    assert (big["load_partitioned"], big["partition_col"], big["partition_size_mb"]) == (
        "true", "order_id", str(q.DEFAULT_PARTITION_SIZE_MB))
    assert not any(k in big for k in nj.RECOMMENDATION_INPUTS)
    small = nj.plan_fields(_discovered("small"))
    assert not any(small.get(c) for c in nj.PARTITION_COLUMNS) and not any(k in small for k in nj.RECOMMENDATION_INPUTS)
    upload = {"src_database": "sales", "load_partitioned": "true", "partition_col": "id", "partition_size_mb": "4096"}
    assert nj.plan_fields(upload) is upload


def test_a_mistyped_load_partitioned_is_refused_not_read_as_false():
    [line] = _user_plan("load_partitioned,partition_col,partition_size_mb", "ture,id,2048")["lines"]
    assert line["status"] == "invalid" and "load_partitioned must be true or false, not 'ture'" in line["errors"]
    [line] = _user_plan("load_partitioned,partition_col,partition_size_mb", "on,id,2048")["lines"]
    assert line["status"] == "valid" and line["row"]["load_partitioned"] is True


def test_a_partition_column_or_size_with_a_blank_load_partitioned_is_refused():
    [line] = _user_plan("load_partitioned,partition_col,partition_size_mb", ",id,4096")["lines"]
    assert line["status"] == "invalid" and any(e.startswith("load_partitioned is blank") for e in line["errors"])
    [line] = _user_plan("load_partitioned,partition_col,partition_size_mb", "false,id,4096")["lines"]
    assert line["status"] == "valid" and line["row"].get("load_partitioned") is None


def test_a_partition_size_must_be_a_whole_number_within_bounds():
    for size, message in (("2048.0", "partition_size_mb must be a whole number of MB, not '2048.0'"),
                          ("3000000000", "partition_size_mb must be a whole number from 1 to 1,048,576 (MB)"),
                          ("0", "partition_size_mb must be a whole number from 1 to 1,048,576 (MB)")):
        [line] = _user_plan("load_partitioned,partition_col,partition_size_mb", f"true,id,{size}")["lines"]
        assert line["status"] == "invalid" and message in line["errors"], (size, line["errors"])
    [line] = _user_plan("load_partitioned,partition_col,partition_size_mb", "true,id,1048576")["lines"]
    assert line["status"] == "valid"


def test_a_partition_column_given_twice_is_refused():
    plan = _user_plan("load_partitioned,load_partitioned,partition_col,partition_size_mb", "true,false,id,2048")
    assert plan["errors"] == ["column load_partitioned is given twice"]
    text = f"{USER_HEADER},load_partitioned,load_partitioned\nsql_a,sales,dbo,t,id,id,true,false\n"
    assert "column load_partitioned is given twice" in nj.read_upload(text, "sql_a")["errors"]

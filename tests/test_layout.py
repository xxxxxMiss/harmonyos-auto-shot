from autoshot.layout import (Viewport, find_by_text, make_pattern,
                             normalize_text, parse_bounds, parse_tree)


def _node(text, bounds, visible="true", clickable="true"):
    return {"attributes": {"text": text, "bounds": bounds, "visible": visible,
                           "clickable": clickable}, "children": []}


def test_parse_bounds():
    assert parse_bounds("[10,20][110,220]") == (10, 20, 110, 220)
    assert parse_bounds("not-a-bound") is None


def test_normalize_ignores_whitespace():
    assert normalize_text("网络 不可用\n") == "网络不可用"


def test_exact_match_beats_contains():
    tree = parse_tree({"attributes": {}, "children": [
        _node("设置中心", "[0,0][100,50]"),
        _node("设置", "[0,100][100,150]"),
    ]})
    hits = find_by_text(tree, ["设置"])
    assert hits[0].node.text == "设置" and hits[0].score == 3


def test_placeholder_pattern_match():
    pat = make_pattern("还剩%d个")
    assert pat is not None and pat.match(normalize_text("还剩3个"))
    tree = parse_tree({"attributes": {}, "children": [_node("还剩3个", "[0,0][100,50]")]})
    hits = find_by_text(tree, ["还剩%d个"])
    assert hits and hits[0].score == 2


def test_invisible_node_skipped():
    tree = parse_tree({"attributes": {}, "children": [
        _node("隐藏项", "[0,0][100,50]", visible="false")]})
    assert find_by_text(tree, ["隐藏项"]) == []


def test_viewport_contains():
    vp = Viewport(1080, 2340, margin_top=96, margin_bottom=96, margin_side=16)
    assert vp.contains((100, 150, 900, 2000))
    assert not vp.contains((100, 50, 900, 200))     # 顶到状态栏/吸顶区
    assert not vp.contains((100, 150, 900, 2300))   # 底部 tab 区


def test_scroll_delta_to_center():
    from autoshot.layout import scroll_delta_to_center
    vp = Viewport(1080, 2340, margin_top=96, margin_bottom=96, margin_side=16)
    center_y = (96 + 2340 - 96) // 2
    # 目标中心在屏幕下半部 → delta 为正（内容需上移）
    assert scroll_delta_to_center((0, 1800, 100, 1900), vp) == (1800 + 1900) // 2 - center_y
    # 目标已居中 → delta 为 0
    assert scroll_delta_to_center((0, center_y - 50, 100, center_y + 50), vp) == 0

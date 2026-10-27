# ---- well_config_ui（天河落共用片段，打包时内联进脚本；须放在 well_config.py 与 settings_ui.py 之后）----
# 「井大样项目配置」图形窗口：图纸名前缀、图号前缀、井号系列、图名模板、图纸集合、图框 logo。
# wcfg_edit() 打开窗口（保存后立即生效）；wcfg_first_run(插件名) 在这台电脑第一次用井大样插件时弹出，让用户先确认配置。


def _wcfg_map_to_lines(d):
    return [u"%s = %s" % (k, v) for k, v in sorted((d or {}).items(), key=lambda t: (-len(t[0]), t[0]))]


def _wcfg_lines_to_map(lines, name):
    out = {}
    for ln in lines:
        if u"=" not in ln:
            raise ValueError(u"「%s」这一行缺少「=」：%s（格式：前缀 = 内容）" % (name, ln))
        k, v = ln.split(u"=", 1)
        k = k.strip(); v = v.strip()
        if not k or not v:
            raise ValueError(u"「%s」这一行不完整：%s" % (name, ln))
        out[k] = v
    return out


def _wcfg_series_to_lines(series):
    return [u"%s | %s | %s | %s" % (s.get(u"prefix", u""), s.get(u"system", u""), s.get(u"code", u""), s.get(u"label", u"")) for s in series or []]


def _wcfg_lines_to_series(lines):
    out = []
    for ln in lines:
        parts = [x.strip() for x in ln.replace(u"｜", u"|").split(u"|")]
        if len(parts) < 3 or not parts[0]:
            raise ValueError(u"井号系列这一行格式不对：%s\n格式：前缀 | 系统名 | 系统代码 | 菜单名（菜单名可省略）" % ln)
        out.append({u"prefix": parts[0], u"system": parts[1] or parts[0], u"code": parts[2] or parts[0],
                    u"label": (parts[3] if len(parts) > 3 and parts[3] else parts[0] + u"-")})
    if not out:
        raise ValueError(u"井号系列至少要有一行。")
    return out


def _wcfg_fields():
    return [
        {u"kind": u"header", u"label": u"图纸命名"},
        {u"key": u"sheet_name_prefix", u"label": u"井大样图纸名前缀", u"kind": u"text", u"required": True,
         u"hint": u"插件按这个前缀认出哪些图纸是井大样，如「井大样_」。"},
        {u"key": u"sheet_number_prefix", u"label": u"图号前缀", u"kind": u"text", u"hint": u"新建图纸的图号前缀，如 AG-。"},
        {u"key": u"indoor_prefix", u"label": u"室内井井号前缀", u"kind": u"text", u"hint": u"如「室内-」，井号形如 室内-A-01。"},
        {u"key": u"system_param", u"label": u"图纸系统参数名", u"kind": u"text", u"hint": u"归类时写入图纸的参数名，用于浏览器按系统分组。"},
        {u"key": u"sheet_name_templates", u"label": u"图名模板（按井号前缀）", u"kind": u"lines", u"rows": 7,
         u"hint": u"每行一条：前缀 = 模板，{井号} 会换成井号。没写模板的井沿用上面的图纸名前缀。"},
        {u"key": u"sheet_collection_by_prefix", u"label": u"图纸集合（按井号前缀）", u"kind": u"lines", u"rows": 7,
         u"hint": u"每行一条：前缀 = 图纸集合名（要与项目里的图纸集合同名），新图放进对应集合，便于按集合导出 PDF。"},
        {u"kind": u"header", u"label": u"井号系列"},
        {u"key": u"series", u"label": u"井号系列", u"kind": u"lines", u"rows": 12,
         u"hint": u"每行一个系列：前缀 | 系统名 | 系统代码 | 菜单名。井号形如 AA-01、T-068；匹配时长前缀优先（AA 先于 A）。顺序就是菜单和排序的顺序。"},
        {u"key": u"other_system", u"label": u"其他井的系统名", u"kind": u"text", u"hint": u"井号不属于以上任何系列时用。"},
        {u"key": u"other_code", u"label": u"其他井的系统代码", u"kind": u"text"},
        {u"kind": u"header", u"label": u"导出 DWG"},
        {u"key": u"dwg_export_setup", u"label": u"DWG 导出设置名", u"kind": u"text", u"hint": u"项目里「导出设置」的名称，找不到时用默认设置。"},
        {u"key": u"logo_enabled", u"label": u"补插图框 logo", u"kind": u"bool", u"bool_text": u"导出 DWG 后在图框里补插光栅 logo"},
        {u"key": u"logo_file", u"label": u"logo 图片文件名", u"kind": u"text", u"hint": u"放在 文档\\KaifaTools 里。"},
        {u"key": u"logo_x0", u"label": u"logo 左下角 X", u"kind": u"num"},
        {u"key": u"logo_y0", u"label": u"logo 左下角 Y", u"kind": u"num"},
        {u"key": u"logo_w", u"label": u"logo 宽度", u"kind": u"num", u"min": 1, u"hint": u"Layout1 里的图纸单位，按 A1 横向图框标定；换图框要改。"},
    ]


def _wcfg_to_flat(cfg):
    logo = cfg.get(u"logo") or {}
    other = cfg.get(u"other") or {}
    return {
        u"sheet_name_prefix": cfg.get(u"sheet_name_prefix", u""),
        u"sheet_number_prefix": cfg.get(u"sheet_number_prefix", u""),
        u"indoor_prefix": cfg.get(u"indoor_prefix", u""),
        u"system_param": cfg.get(u"system_param", u""),
        u"sheet_name_templates": _wcfg_map_to_lines(cfg.get(u"sheet_name_templates")),
        u"sheet_collection_by_prefix": _wcfg_map_to_lines(cfg.get(u"sheet_collection_by_prefix")),
        u"series": _wcfg_series_to_lines(cfg.get(u"series")),
        u"other_system": other.get(u"system", u""),
        u"other_code": other.get(u"code", u""),
        u"dwg_export_setup": cfg.get(u"dwg_export_setup") or u"机电导图设置",
        u"logo_enabled": bool(logo.get(u"enabled", True)),
        u"logo_file": logo.get(u"file", u"frame_logo.jpg"),
        u"logo_x0": logo.get(u"x0", 0), u"logo_y0": logo.get(u"y0", 0), u"logo_w": logo.get(u"w", 0),
    }


def wcfg_edit(intro=None, title=u"井大样项目配置"):
    """打开项目配置窗口。保存返回 True（并写回 文档\\KaifaTools\\井大样配置.json），取消返回 False。"""
    cur = wcfg_load()
    vals = _wcfg_to_flat(cur)
    defaults = _wcfg_to_flat(_wcfg_merge(WCFG_DEFAULTS, {}))
    intro = intro or (u"井大样系列插件（一键井展开图、井大样标注、图纸归类、井图号重编、导出井大样 DWG 等）共用这份配置。"
                      u"换项目时改这里；保存后下次点插件生效。")
    while True:
        res = su_fields_dialog(title, intro, _wcfg_fields(), vals, defaults,
                               extra_buttons=[(u"用记事本打开文件", lambda: su_open_file(WCFG_FILE))], width=760, height=700)
        if res is None:
            return False
        try:
            series = _wcfg_lines_to_series(res[u"series"])
            tpl = _wcfg_lines_to_map(res[u"sheet_name_templates"], u"图名模板")
            col = _wcfg_lines_to_map(res[u"sheet_collection_by_prefix"], u"图纸集合")
        except ValueError as ex:
            _SUW.MessageBox.Show(u"%s" % ex, title)
            vals = res
            continue
        break
    # 在原文件内容上改（保留文件里其它键和说明），没有文件就从默认值开始
    data = _wcfg_merge(WCFG_DEFAULTS, {})
    try:
        if _wc_os.path.exists(WCFG_FILE):
            with _wc_io.open(WCFG_FILE, 'r', encoding='utf-8-sig') as f:
                data = _wcfg_merge(data, _wc_json.load(f))
    except Exception:
        pass
    data[u"sheet_name_prefix"] = res[u"sheet_name_prefix"]
    data[u"sheet_number_prefix"] = res[u"sheet_number_prefix"]
    data[u"indoor_prefix"] = res[u"indoor_prefix"]
    data[u"system_param"] = res[u"system_param"]
    data[u"series"] = series
    data[u"sheet_name_templates"] = tpl
    data[u"sheet_collection_by_prefix"] = col
    data[u"other"] = {u"system": res[u"other_system"] or u"其他", u"code": res[u"other_code"] or u"QT"}
    data[u"dwg_export_setup"] = res[u"dwg_export_setup"]
    logo = dict(data.get(u"logo") or {})
    logo.update({u"enabled": res[u"logo_enabled"], u"file": res[u"logo_file"], u"x0": res[u"logo_x0"],
                 u"y0": res[u"logo_y0"], u"w": res[u"logo_w"]})
    data[u"logo"] = logo
    d = _wc_os.path.dirname(WCFG_FILE)
    if not _wc_os.path.isdir(d):
        _wc_os.makedirs(d)
    with _wc_io.open(WCFG_FILE, 'w', encoding='utf-8') as f:
        f.write(_wc_json.dumps(data, ensure_ascii=False, indent=2))
    _WCFG_CACHE[0] = None
    return True


def wcfg_first_run(plugin_title):
    """这台电脑第一次用井大样插件时，先请用户确认项目配置。
    返回 True：照常运行（用户点了取消，先用默认规则）；返回 False：配置刚保存，插件应退出、请用户再点一次（脚本开头已按旧配置算好的值要重新读）。"""
    if not WCFG_FIRST_RUN:
        return True
    globals()[u"WCFG_FIRST_RUN"] = False
    intro = (u"第一次使用「%s」：井大样系列插件按这里的规则认图纸、认井号、给新图纸命名。"
             u"下面是默认规则（来自示例项目），请按你的项目改好再保存；点取消则先按默认规则运行。"
             u"以后可以在「井大样项目配置」里随时修改。" % plugin_title)
    if wcfg_edit(intro=intro, title=u"井大样项目配置（第一次使用）"):
        _SUW.MessageBox.Show(u"项目配置已保存。请再点一次「%s」，按新配置运行。" % plugin_title, u"井大样项目配置")
        return False
    return True

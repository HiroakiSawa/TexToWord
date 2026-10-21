-- tex2docx pandoc filter.
--  * marker Spans (\hypertarget{tdx-m-N}{...}) -> Word character / paragraph styles
--  * raw LaTeX that pandoc could not interpret -> highlighted "[[未変換: ...]]" text
--  * heading levels shifted so that the top level is Heading 1
-- Settings are read from tdx-filter.json in the working directory (relative path,
-- so that non-ASCII directory names work with Lua's io.open on Windows).

local cfg

local function log(kind, msg)
  msg = msg:gsub("[\r\n\t]+", " ")
  io.stderr:write("TDXLOG\t" .. kind .. "\t" .. msg .. "\n")
end

local function load_cfg()
  local f = io.open("tdx-filter.json", "rb")
  if not f then
    error("tdx-filter.json not found in working directory")
  end
  local s = f:read("a")
  f:close()
  cfg = pandoc.json.decode(s, false)
  cfg.ignore_set = {}
  for _, name in ipairs(cfg.ignore or {}) do
    cfg.ignore_set[name] = true
  end
end

local function unconverted_inline(text)
  return pandoc.Span({ pandoc.Str("[[未変換: " .. text .. "]]") },
    { ["custom-style"] = cfg.styles.unconverted })
end

local function raw_name(text)
  local env = text:match("^\\begin%s*{([^}]+)}")
  if env then
    return env, true
  end
  local name = text:match("^\\([%a@]+)")
  if name then
    return name, false
  end
  return text:match("^\\(.)"), false
end

local function is_latex(fmt)
  return fmt == "latex" or fmt == "tex"
end

local function RawInline(el)
  if not is_latex(el.format) then
    return nil
  end
  local name = raw_name(el.text)
  if name and cfg.ignore_set[name] then
    log("ignored", el.text)
    return {}
  end
  log("unconverted", el.text)
  return unconverted_inline(el.text)
end

local function RawBlock(el)
  if not is_latex(el.format) then
    return nil
  end
  local name = raw_name(el.text)
  if name and cfg.ignore_set[name] then
    log("ignored", el.text)
    return {}
  end
  log("unconverted", el.text)
  local blocks = {}
  local text = el.text:gsub("\r\n", "\n")
  for chunk in (text .. "\n\n"):gmatch("(.-)\n%s*\n") do
    if chunk:match("%S") then
      table.insert(blocks, pandoc.Para({ unconverted_inline(chunk:gsub("\n", " ")) }))
    end
  end
  return blocks
end

local function Span(el)
  local m = cfg.markers[el.identifier]
  if not m or m.type ~= "char" then
    return nil
  end
  el.identifier = ""
  el.attributes["custom-style"] = m.style
  return el
end

-- paragraph markers: an (empty) marker span at the start of a paragraph
local function take_para_marker(inlines)
  for i, il in ipairs(inlines) do
    if il.t == "Span" then
      local m = cfg.markers[il.identifier]
      if m and m.type == "para" then
        inlines:remove(i)
        if inlines[i] and inlines[i].t == "Space" then
          inlines:remove(i)
        end
        return m.style
      end
      return nil
    elseif il.t ~= "Space" and il.t ~= "SoftBreak" then
      return nil
    end
  end
  return nil
end

local function block_with_marker(el)
  local style = take_para_marker(el.content)
  if style then
    return pandoc.Div({ el }, { ["custom-style"] = style })
  end
  return nil
end

local function shift_headers(doc)
  local min = 99
  doc:walk({
    Header = function(h)
      if h.level < min then
        min = h.level
      end
    end,
  })
  if min > 1 and min < 99 then
    local delta = min - 1
    doc = doc:walk({
      Header = function(h)
        h.level = h.level - delta
        return h
      end,
    })
  end
  return doc
end

function Pandoc(doc)
  load_cfg()
  doc = doc:walk({
    RawInline = RawInline,
    RawBlock = RawBlock,
    Span = Span,
  })
  doc = doc:walk({
    Para = block_with_marker,
    Plain = block_with_marker,
  })
  return shift_headers(doc)
end

#!/usr/bin/env python3
"""Fix index.html: add interview tab case, export button bindings, search binding."""
import sys

filepath = "app/web/templates/index.html"

with open(filepath, "r", encoding="utf-8") as f:
    content = f.read()

lines = content.split("\n")
line152 = lines[151]

# 1. Check what tab cases exist
tabs_found = []
for tab_name in ['tasks', 'timeline', 'matrix', 'workload', 'report', 'interview']:
    if f"tab==='{tab_name}'" in line152:
        tabs_found.append(tab_name)
print(f"Tab cases found in renderResultTab: {tabs_found}")

# 2. Add interview case if missing
if 'interview' not in tabs_found:
    # Find the position to insert - after the last tab case, before bindStatusControls
    # We need to find where the function ends and insert before bindStatusControls()
    # The pattern is: el('resultContent').innerHTML=content;bindStatusControls()
    # We need to insert: if(tab==='interview')content=...; before el('resultContent')
    
    interview_html = (
        "if(tab==='interview')content="
        "'<div class=\"interview-panel\">"
        "<div class=\"interview-head\"><h3>答辩模拟</h3><p>系统根据任务分工生成模拟问题，帮助团队预演答辩。</p></div>"
        "<button id=\"startInterviewBtn\" class=\"btn btn-primary\">生成模拟问题</button>"
        "<div id=\"interviewQuestions\" class=\"interview-list\"></div></div>';"
    )
    
    # Find the insertion point - right before el('resultContent').innerHTML=content
    anchor = "el('resultContent').innerHTML=content"
    if anchor in line152:
        line152 = line152.replace(anchor, interview_html + anchor)
        lines[151] = line152
        print(">>> Added interview tab case to renderResultTab")
    else:
        print(">>> ERROR: Could not find insertion point for interview case")
else:
    print(">>> Interview tab case already exists")

# 3. Add export button and search event bindings
# Find the line with el('saveBtn').onclick=savePlan
binding_line_idx = None
for i, line in enumerate(lines):
    if "el('saveBtn').onclick=savePlan" in line:
        binding_line_idx = i
        break

if binding_line_idx is not None:
    binding_line = lines[binding_line_idx]
    # Check if export bindings already exist
    if 'exportMdBtn' not in binding_line:
        new_bindings = (
            "el('exportMdBtn').onclick=function(){exportPlan('markdown')};"
            "el('exportDocxBtn').onclick=function(){exportPlan('docx')};"
            "el('exportPdfBtn').onclick=function(){exportPlan('pdf')};"
            "el('planSearch').oninput=function(){showHistory(el('planSearch').value)};"
        )
        # Insert before el('saveBtn').onclick=savePlan
        binding_line = binding_line.replace(
            "el('saveBtn').onclick=savePlan",
            new_bindings + "el('saveBtn').onclick=savePlan"
        )
        lines[binding_line_idx] = binding_line
        print(f">>> Added export button and search bindings to line {binding_line_idx + 1}")
    else:
        print(">>> Export bindings already exist")
else:
    print(">>> ERROR: Could not find saveBtn binding line")

# 4. Enable export buttons when plan is available
# In confirmAssignment, after el('saveBtn').disabled=false, add export button enabling
for i, line in enumerate(lines):
    if 'confirmAssignment' in line and 'async function' in line:
        if 'exportMdBtn' not in line:
            line = line.replace(
                "el('saveBtn').disabled=false",
                "el('saveBtn').disabled=false;el('exportMdBtn').disabled=false;el('exportDocxBtn').disabled=false;el('exportPdfBtn').disabled=false"
            )
            lines[i] = line
            print(f">>> Enabled export buttons in confirmAssignment (line {i+1})")
        break

# In loadPlan, after el('saveBtn').disabled=false, add export button enabling
for i, line in enumerate(lines):
    if 'async function loadPlan' in line:
        if 'exportMdBtn' not in line:
            line = line.replace(
                "el('saveBtn').disabled=false",
                "el('saveBtn').disabled=false;el('exportMdBtn').disabled=false;el('exportDocxBtn').disabled=false;el('exportPdfBtn').disabled=false"
            )
            lines[i] = line
            print(f">>> Enabled export buttons in loadPlan (line {i+1})")
        break

# 5. Also call bindInterviewControls after rendering interview tab
# In renderResultTab, after bindStatusControls(), add bindInterviewControls()
for i, line in enumerate(lines):
    if 'function renderResultTab' in line:
        if 'bindInterviewControls' not in line:
            line = line.replace(
                "bindStatusControls()",
                "bindStatusControls();if(tab==='interview')bindInterviewControls()"
            )
            lines[i] = line
            print(f">>> Added bindInterviewControls call in renderResultTab (line {i+1})")
        break

# Write back
new_content = "\n".join(lines)
with open(filepath, "w", encoding="utf-8") as f:
    f.write(new_content)

print("\n>>> All modifications applied successfully!")
print(f"File size: {len(new_content)} bytes")

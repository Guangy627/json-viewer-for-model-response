#!/usr/bin/env python3
"""
JSONL Viewer CLI - 查看和分析 JSONL 文件（特别是 Claude Code 日志）

命令示例:
  jv file.jsonl                    # 查看摘要
  jv file.jsonl -l 5               # 查看第5行
  jv file.jsonl -t assistant       # 筛选类型
  jv file.jsonl --analyze          # 深度分析（Claude Code 日志）
  jv file.jsonl -k type,model      # 只显示指定字段
  jv file.jsonl --code             # 提取 agent 写入/修改的代码
"""

import json
import sys
import argparse
from pathlib import Path
from typing import List, Dict, Any, Optional


def load_jsonl(filepath: str) -> List[Dict[str, Any]]:
    """加载 JSONL 文件"""
    records = []
    with open(filepath, 'r', encoding='utf-8') as f:
        for i, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError as e:
                print(f"⚠️  行 {i} JSON 解析失败: {e}", file=sys.stderr)
    return records


def load_trajectory_json(filepath: str) -> List[Dict[str, Any]]:
    """加载 trajectory.json 格式（完整 JSON 文件，包含 steps 数组）"""
    with open(filepath, 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    # trajectory 格式的 steps 在顶层
    if isinstance(data, dict) and 'steps' in data:
        return data.get('steps', [])
    
    # 如果是数组，直接返回
    if isinstance(data, list):
        return data
    
    return [data]


def smart_load(filepath: str) -> tuple[List[Dict[str, Any]], str]:
    """
    智能加载文件，自动识别格式
    返回: (records, format_type)
    format_type: 'jsonl' | 'trajectory' | 'unknown'
    """
    # 先尝试作为完整 JSON 加载
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read().strip()
            if content.startswith('{'):
                data = json.loads(content)
                if isinstance(data, dict) and 'steps' in data:
                    return data.get('steps', []), 'trajectory'
    except:
        pass
    
    # 否则作为 JSONL 加载
    records = load_jsonl(filepath)
    return records, 'jsonl'


def print_json(obj: Any, truncate: Optional[int] = None):
    """美化打印 JSON"""
    output = json.dumps(obj, indent=2, ensure_ascii=False)
    if truncate and len(output) > truncate:
        output = output[:truncate] + "\n... (已截断)"
    print(output)


def show_summary(records: List[Dict], filepath: str):
    """显示文件摘要"""
    print(f"📄 文件: {filepath}")
    print(f"📊 总行数: {len(records)}")
    print()
    
    # 统计 type
    types = {}
    for r in records:
        t = r.get('type', 'unknown')
        types[t] = types.get(t, 0) + 1
    
    print("消息类型统计:")
    for t, c in sorted(types.items(), key=lambda x: -x[1]):
        print(f"  {t}: {c}")
    
    # 检查是否是 Claude Code 日志
    if any(r.get('type') == 'system' and r.get('subtype') == 'init' for r in records):
        print()
        print("💡 检测到 Claude Code 日志，使用 --analyze 查看详细分析")


def show_line(records: List[Dict], line_num: int, truncate: Optional[int] = None):
    """显示指定行"""
    idx = line_num - 1
    if 0 <= idx < len(records):
        print(f"=== 行 {line_num} ===")
        print_json(records[idx], truncate)
    else:
        print(f"❌ 行号超出范围 (1-{len(records)})", file=sys.stderr)
        sys.exit(1)


def show_by_type(records: List[Dict], type_filter: str, truncate: Optional[int] = None):
    """按类型筛选显示"""
    found = False
    for i, r in enumerate(records, 1):
        if r.get('type') == type_filter:
            found = True
            print(f"=== 行 {i} ===")
            print_json(r, truncate)
            print()
    
    if not found:
        print(f"❌ 没有找到 type='{type_filter}' 的记录", file=sys.stderr)


def show_keys(records: List[Dict], keys: List[str]):
    """只显示指定的 key"""
    for i, r in enumerate(records, 1):
        extracted = {}
        for k in keys:
            # 支持嵌套 key，如 message.usage.input_tokens
            parts = k.split('.')
            val = r
            for p in parts:
                if isinstance(val, dict):
                    val = val.get(p)
                else:
                    val = None
                    break
            if val is not None:
                extracted[k] = val
        
        if extracted:
            print(f"行 {i}: {json.dumps(extracted, ensure_ascii=False)}")


def extract_agent_code(records: List[Dict], output_dir: Optional[str] = None, format_type: str = 'jsonl'):
    """
    提取 agent 实现的代码
    
    支持两种格式:
    1. Claude Code 日志 (claude-code.txt): Edit/Write 工具
    2. Trajectory 日志 (trajectory.json): Write 函数
    """
    print("=" * 60)
    print("📝 Agent 代码提取")
    print("=" * 60)
    
    code_changes = []  # [(step_id, tool_name, file_path, action, content)]
    
    for i, r in enumerate(records, 1):
        # Claude Code JSONL 格式: type=assistant, message.content[].type=tool_use
        if r.get('type') == 'assistant':
            msg = r.get('message', {})
            for c in msg.get('content', []):
                if c.get('type') == 'tool_use':
                    tool_name = c.get('name', '')
                    tool_input = c.get('input', {})
                    
                    if tool_name == 'Write':
                        file_path = tool_input.get('file_path', 'unknown')
                        content = tool_input.get('content', '')
                        code_changes.append((i, 'Write', file_path, 'create', content))
                    
                    elif tool_name == 'Edit':
                        file_path = tool_input.get('file_path', 'unknown')
                        old_str = tool_input.get('old_string', '')
                        new_str = tool_input.get('new_string', '')
                        code_changes.append((i, 'Edit', file_path, 'modify', 
                                           f"--- OLD ---\n{old_str}\n\n+++ NEW +++\n{new_str}"))
        
        # Trajectory JSONL 格式: tool_calls[].function_name=Write
        tool_calls = r.get('tool_calls', [])
        for tc in tool_calls:
            func_name = tc.get('function_name', '')
            args = tc.get('arguments', {})
            
            if func_name == 'Write':
                file_path = args.get('file_path', 'unknown')
                content = args.get('content', '')
                code_changes.append((i, 'Write', file_path, 'create', content))
        
        # Trajectory JSON 格式 (steps 数组): tool_calls 在 step 中
        if format_type == 'trajectory':
            step_id = r.get('step_id', i)
            step_tool_calls = r.get('tool_calls', [])
            for tc in step_tool_calls:
                func_name = tc.get('function_name', '')
                args = tc.get('arguments', {})
                
                if func_name == 'Write':
                    file_path = args.get('file_path', 'unknown')
                    content = args.get('content', '')
                    code_changes.append((step_id, 'Write', file_path, 'create', content))
                
                elif func_name == 'Edit':
                    file_path = args.get('file_path', 'unknown')
                    old_str = args.get('old_string', '')
                    new_str = args.get('new_string', '')
                    code_changes.append((step_id, 'Edit', file_path, 'modify',
                                       f"--- OLD ---\n{old_str}\n\n+++ NEW +++\n{new_str}"))
    
    if not code_changes:
        print("\n❌ 没有找到 agent 写入或修改代码的记录")
        return
    
    # 统计
    print(f"\n📊 找到 {len(code_changes)} 处代码变更:")
    
    # 按文件分组统计
    file_stats = {}
    for _, tool, file_path, action, _ in code_changes:
        if file_path not in file_stats:
            file_stats[file_path] = {'Write': 0, 'Edit': 0}
        file_stats[file_path][tool] = file_stats[file_path].get(tool, 0) + 1
    
    print("\n【文件列表】")
    for fp, stats in file_stats.items():
        parts = []
        if stats.get('Write', 0) > 0:
            parts.append(f"{stats['Write']} 次写入")
        if stats.get('Edit', 0) > 0:
            parts.append(f"{stats['Edit']} 次编辑")
        print(f"  {fp}: {', '.join(parts)}")
    
    # 显示每个代码变更
    print("\n" + "=" * 60)
    print("【代码详情】")
    print("=" * 60)
    
    for idx, (line_no, tool, file_path, action, content) in enumerate(code_changes, 1):
        print(f"\n{'─' * 60}")
        print(f"📄 [{idx}/{len(code_changes)}] 行 {line_no}: {tool} → {file_path}")
        print(f"{'─' * 60}")
        
        # 如果内容太长，截取显示
        if len(content) > 3000:
            print(content[:3000])
            print(f"\n... (共 {len(content)} 字符，已截断)")
        else:
            print(content)
    
    # 如果指定了输出目录，保存文件
    if output_dir:
        from pathlib import Path
        import os
        
        out_path = Path(output_dir)
        out_path.mkdir(parents=True, exist_ok=True)
        
        print(f"\n\n💾 保存代码到目录: {output_dir}")
        
        for idx, (line_no, tool, file_path, action, content) in enumerate(code_changes, 1):
            # 生成安全的文件名
            safe_name = file_path.replace('/', '_').replace('\\', '_').lstrip('_')
            if tool == 'Edit':
                safe_name = f"{idx:03d}_edit_{safe_name}.diff"
            else:
                safe_name = f"{idx:03d}_write_{safe_name}"
            
            save_path = out_path / safe_name
            with open(save_path, 'w', encoding='utf-8') as f:
                f.write(f"# Source: line {line_no}, tool: {tool}\n")
                f.write(f"# Target: {file_path}\n\n")
                f.write(content)
            print(f"  ✅ {safe_name}")


def analyze_claude_code(records: List[Dict]):
    """深度分析 Claude Code 日志"""
    print("=" * 60)
    print("🔍 Claude Code 日志深度分析")
    print("=" * 60)
    
    # 1. 基本信息
    for r in records:
        if r.get('type') == 'system' and r.get('subtype') == 'init':
            print()
            print("【会话信息】")
            print(f"  模型: {r.get('model')}")
            print(f"  版本: {r.get('claude_code_version')}")
            print(f"  工作目录: {r.get('cwd')}")
            print(f"  工具数: {len(r.get('tools', []))}")
            break
    
    # 2. 最终结果
    for r in records:
        if r.get('type') == 'result':
            print()
            print("【执行结果】")
            print(f"  状态: {'✅ 成功' if r.get('subtype') == 'success' else '❌ 失败'}")
            print(f"  耗时: {r.get('duration_ms', 0) / 1000:.1f}秒")
            print(f"  轮数: {r.get('num_turns')}")
            print(f"  花费: ${r.get('total_cost_usd', 0):.4f}")
            
            usage = r.get('usage', {})
            print()
            print("【Token 使用】")
            print(f"  输入: {usage.get('input_tokens', 0)}")
            print(f"  输出: {usage.get('output_tokens', 0)}")
            print(f"  缓存读取: {usage.get('cache_read_input_tokens', 0)}")
            print(f"  缓存创建: {usage.get('cache_creation_input_tokens', 0)}")
            break
    
    # 3. 工具使用统计
    tool_uses = {}
    errors = []
    
    for i, r in enumerate(records, 1):
        if r.get('type') == 'assistant':
            msg = r.get('message', {})
            for c in msg.get('content', []):
                if c.get('type') == 'tool_use':
                    name = c.get('name')
                    tool_uses[name] = tool_uses.get(name, 0) + 1
        
        if r.get('type') == 'user':
            msg = r.get('message', {})
            for c in msg.get('content', []):
                if c.get('is_error'):
                    errors.append((i, c.get('content', '')[:80]))
    
    print()
    print("【工具调用】")
    for name, count in sorted(tool_uses.items(), key=lambda x: -x[1]):
        print(f"  {name}: {count}次")
    
    if errors:
        print()
        print(f"【错误 ({len(errors)}个)】")
        for line_no, err in errors:
            print(f"  行{line_no}: {err}...")
    
    # 4. 模型思考过程
    print()
    print("【思考过程】")
    for i, r in enumerate(records, 1):
        if r.get('type') == 'assistant':
            msg = r.get('message', {})
            for c in msg.get('content', []):
                if c.get('type') == 'text':
                    text = c.get('text', '')[:60]
                    print(f"  行{i}: {text}...")
                    break


def main():
    parser = argparse.ArgumentParser(
        description='JSONL 文件查看器 (支持 Claude Code 日志分析)',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📖 使用示例
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  基本用法:
    jv file.jsonl                      查看文件摘要
    jv file.jsonl -l 5                 查看第5行（美化输出）
    jv file.jsonl -l 5 --truncate 500  查看第5行（截断到500字符）

  按类型筛选:
    jv file.jsonl -t assistant         筛选 AI 输出
    jv file.jsonl -t user              筛选工具执行结果
    jv file.jsonl -t result            查看最终统计
    jv file.jsonl -t system            查看初始化配置

  提取特定字段:
    jv file.jsonl -k type,model        只显示 type 和 model
    jv file.jsonl -k message.usage.input_tokens   支持嵌套字段

  Claude Code 日志分析:
    jv claude-code.txt -a              一键深度分析（推荐！）

  提取 Agent 代码:
    jv claude-code.txt -c              查看 agent 写入/修改的所有代码
    jv trajectory.json -c              也支持 trajectory 格式
    jv claude-code.txt -c -o ./codes   提取代码并保存到目录

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🔑 Claude Code 日志关键字段说明
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  消息类型 (type):
    system     - 初始化配置（模型、工具、版本等）
    assistant  - AI 输出（思考过程 + 工具调用）
    user       - 工具执行结果
    result     - 最终汇总（耗时、花费、token 统计）

  重要字段:
    result.total_cost_usd              💰 总花费
    result.duration_ms                 ⏱️  总耗时
    result.num_turns                   🔄 对话轮数
    result.usage.output_tokens         📤 输出 token 数
    result.usage.cache_read_input_tokens   📦 缓存命中
    assistant.message.content[].type   📝 text=思考 / tool_use=调用工具
    user.message.content[].is_error    ❌ 工具是否出错

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
💡 小技巧
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  • JSONL 文件可以是 .jsonl 或 .txt 后缀，都能识别
  • 用 -a 快速分析 Claude Code 日志的行为和花费
  • 用 -c 提取 agent 写入的代码，支持 claude-code.txt 和 trajectory.json
  • 用 -t result 快速查看任务是否成功及花费
  • 用 -k 提取字段可以配合其他命令处理，如:
      jv file.jsonl -k type | sort | uniq -c
        """
    )
    parser.add_argument('file', help='JSONL 文件路径')
    parser.add_argument('-l', '--line', type=int, help='查看指定行号')
    parser.add_argument('-t', '--type', dest='type_filter', help='按 type 字段筛选')
    parser.add_argument('-k', '--keys', help='只显示指定字段（逗号分隔，支持嵌套如 message.usage）')
    parser.add_argument('--analyze', '-a', action='store_true', help='深度分析 Claude Code 日志')
    parser.add_argument('--code', '-c', action='store_true', help='提取 agent 写入/修改的代码')
    parser.add_argument('--output', '-o', type=str, default=None, help='保存提取的代码到指定目录')
    parser.add_argument('--truncate', type=int, default=None, help='截断输出到指定字符数')
    parser.add_argument('--version', '-v', action='version', version='%(prog)s 0.2.0')
    
    args = parser.parse_args()
    
    # 检查文件存在
    if not Path(args.file).exists():
        print(f"❌ 文件不存在: {args.file}", file=sys.stderr)
        sys.exit(1)
    
    # 加载数据（智能识别格式）
    records, format_type = smart_load(args.file)
    if not records:
        print("❌ 文件为空或没有有效的 JSON 行", file=sys.stderr)
        sys.exit(1)
    
    # 执行对应操作
    if args.line:
        show_line(records, args.line, args.truncate)
    elif args.type_filter:
        show_by_type(records, args.type_filter, args.truncate)
    elif args.keys:
        keys = [k.strip() for k in args.keys.split(',')]
        show_keys(records, keys)
    elif args.analyze:
        analyze_claude_code(records)
    elif args.code:
        extract_agent_code(records, args.output, format_type)
    else:
        show_summary(records, args.file)


if __name__ == '__main__':
    main()

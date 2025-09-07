import requests
import json
import time
import re
from typing import Dict, List, Optional
from config import Config

class GLMClient:
    def __init__(self, api_key: str = None):
        self.api_key = api_key or Config.GLM_API_KEY
        self.base_url = Config.GLM_BASE_URL
        self.session = requests.Session()
        self.session.headers.update({
            'Authorization': f'Bearer {self.api_key}',
            'Content-Type': 'application/json'
        })
        
        # 预过滤规则 - 跳过明显无错误的文本
        self.skip_patterns = [
            r'^[a-zA-Z0-9\s\.,;:!?()-]+$',  # 纯英文/数字
            r'^\d+[年月日时分秒]+$',         # 纯时间
            r'^[0-9\.,\s%]+$',              # 纯数字
            r'^[好的|是的|嗯|啊|哦|对|不是|没有|有]\s*[。！？]*$',  # 简单回应
        ]
        
        # 常见错误模式 - 可以快速修正的（保守策略）
        self.quick_fixes = {
            '明显同音字错误': {
                '发声': '发生',
                '做实': '坐实', 
                '因该': '应该',
                '再那': '在那',
                '在想': '再想',
                '在说一遍': '再说一遍',
                '在问一下': '再问一下',
                '在看看': '再看看',
                '在试试': '再试试',
            },
            '明显错字': {
                '我觉的': '我觉得',
                '说的好': '说得好',
                '做的不错': '做得不错',
                '听的很清楚': '听得很清楚',
                '跑的很快': '跑得很快',
                '说的对': '说得对',
                '想的周到': '想得周到',
            }
        }

    def pre_filter_text(self, text: str) -> bool:
        """
        预过滤：判断文本是否需要调用API
        返回True表示需要调用API，False表示跳过
        """
        text = text.strip()
        
        # 太短的文本跳过
        if len(text) < 3:
            return False
        
        # 匹配跳过模式
        for pattern in self.skip_patterns:
            if re.match(pattern, text):
                return False
        
        # 检查是否包含常见错误字符
        error_indicators = ['的得地', '在再', '那哪', '做坐', '发声', '因该']
        has_potential_error = any(indicator in text for indicator in ['的', '得', '地', '在', '再', '那', '哪'])
        
        if not has_potential_error and len(text) < 20:
            return False
            
        return True

    def quick_fix_text(self, text: str) -> Dict:
        """
        快速修正常见错误，避免API调用
        """
        original_text = text
        corrected_text = text
        errors = []
        
        # 应用快速修正规则
        for error_type, fixes in self.quick_fixes.items():
            for pattern, replacement in fixes.items():
                if isinstance(pattern, str) and pattern in corrected_text:
                    # 简单字符串替换
                    new_text = corrected_text.replace(pattern, replacement)
                    if new_text != corrected_text:
                        errors.append({
                            'type': error_type,
                            'original': pattern,
                            'corrected': replacement,
                            'confidence': 0.9,
                            'reason': f'常见{error_type}错误'
                        })
                        corrected_text = new_text
                        
                elif hasattr(re, 'sub'):
                    # 正则表达式替换
                    try:
                        new_text = re.sub(pattern, replacement, corrected_text)
                        if new_text != corrected_text:
                            errors.append({
                                'type': error_type,
                                'original': '匹配模式',
                                'corrected': '修正模式', 
                                'confidence': 0.8,
                                'reason': f'{error_type}语法修正'
                            })
                            corrected_text = new_text
                    except:
                        continue
        
        has_errors = len(errors) > 0
        
        return {
            'original_text': original_text,
            'corrected_text': corrected_text,
            'has_errors': has_errors,
            'confidence': 0.9 if has_errors else 1.0,
            'errors': errors,
            'suggestions': '快速修正' if has_errors else '无需修正',
            'method': 'quick_fix'
        }

    def detect_and_correct_text_errors(self, text_segment: str, context: str = "") -> Dict:
        """
        检测文本段落中的错误并自动修正
        优化版本：先尝试快速修正，必要时才调用API
        """
        # 1. 预过滤检查
        if not self.pre_filter_text(text_segment):
            return {
                'original_text': text_segment,
                'corrected_text': text_segment,
                'has_errors': False,
                'confidence': 1.0,
                'errors': [],
                'suggestions': '预过滤跳过',
                'method': 'pre_filter'
            }
        
        # 2. 尝试快速修正
        quick_result = self.quick_fix_text(text_segment)
        
        # 如果快速修正找到了错误，且文本较短，直接返回
        if quick_result['has_errors'] and len(text_segment) < 50:
            return quick_result
        
        # 3. 如果文本较长或快速修正没找到问题，使用API
        # 但是使用更精简的prompt
        prompt = f"""检测并修正语音转录错误：

原文："{text_segment}"

只修正明显错字，保持原意。返回JSON：
{{"original_text": "原文", "corrected_text": "修正文本", "has_errors": true/false, "confidence": 0.0-1.0}}

重点检测：同音字错误、的得地误用、明显笔误。不确定的不要改。"""

        try:
            response = self.session.post(
                f'{self.base_url}chat/completions',
                json={
                    "model": "glm-4.5",
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.1,
                    "max_tokens": 500  # 大幅减少max_tokens
                },
                timeout=20  # 减少超时时间
            )
            
            if response.status_code == 200:
                result = response.json()
                content = result['choices'][0]['message']['content']
                
                try:
                    # 提取JSON
                    json_start = content.find('{')
                    json_end = content.rfind('}') + 1
                    if json_start != -1 and json_end != 0:
                        json_content = content[json_start:json_end]
                        parsed_result = json.loads(json_content)
                    else:
                        parsed_result = json.loads(content)
                    
                    # 合并快速修正的结果
                    if quick_result['has_errors']:
                        if parsed_result.get('has_errors'):
                            # 两种方法都发现错误，合并
                            parsed_result['errors'] = quick_result['errors'] + parsed_result.get('errors', [])
                        else:
                            # 只有快速修正发现错误
                            return quick_result
                    
                    parsed_result['method'] = 'api'
                    if 'original_text' not in parsed_result:
                        parsed_result['original_text'] = text_segment
                    
                    return parsed_result
                    
                except json.JSONDecodeError:
                    # API解析失败，返回快速修正结果
                    if quick_result['has_errors']:
                        return quick_result
                    else:
                        return {
                            'original_text': text_segment,
                            'corrected_text': text_segment,
                            'has_errors': False,
                            'confidence': 0.5,
                            'errors': [],
                            'suggestions': 'API解析失败，保持原文',
                            'method': 'fallback'
                        }
            else:
                # API调用失败，返回快速修正结果
                return quick_result if quick_result['has_errors'] else {
                    'original_text': text_segment,
                    'error': f'API调用失败: {response.status_code}',
                    'has_errors': False,
                    'confidence': 0.0
                }
                
        except Exception as e:
            # 异常时返回快速修正结果
            return quick_result if quick_result['has_errors'] else {
                'original_text': text_segment,
                'error': f'处理异常: {str(e)}',
                'has_errors': False,
                'confidence': 0.0
            }

    def batch_detect_and_correct_segments(self, segments: List[Dict], max_retries: int = 2) -> List[Dict]:
        """
        批量处理优化版本
        """
        results = []
        total_segments = len(segments)
        api_calls = 0
        quick_fixes = 0
        skipped = 0
        
        for i, segment in enumerate(segments):
            if (i + 1) % 50 == 0:  # 每50个段落显示一次进度
                print(f"处理进度: {i+1}/{total_segments} (API调用: {api_calls}, 快速修正: {quick_fixes}, 跳过: {skipped})")
            
            text = segment.get('text', '')
            if not text.strip():
                result = segment.copy()
                result.update({
                    'has_errors': False,
                    'confidence': 1.0,
                    'corrected_text': text,
                    'method': 'empty'
                })
                results.append(result)
                skipped += 1
                continue
            
            # 处理文本
            for attempt in range(max_retries):
                result = self.detect_and_correct_text_errors(text)
                
                # 统计调用类型
                method = result.get('method', 'unknown')
                if method == 'api':
                    api_calls += 1
                elif method == 'quick_fix':
                    quick_fixes += 1
                elif method in ['pre_filter', 'empty']:
                    skipped += 1
                
                if 'error' not in result:
                    break
                    
                if attempt < max_retries - 1:
                    time.sleep(0.5)  # 减少重试等待时间
                else:
                    print(f"段落 {i+1} 处理失败: {result.get('error', 'Unknown error')}")
            
            final_result = segment.copy()
            final_result.update(result)
            results.append(final_result)
            
            # 减少API调用间隔
            if result.get('method') == 'api':
                time.sleep(0.05)  # 从0.1秒减少到0.05秒
        
        print(f"\n处理完成统计:")
        print(f"  总段落: {total_segments}")
        print(f"  API调用: {api_calls}")
        print(f"  快速修正: {quick_fixes}")
        print(f"  跳过处理: {skipped}")
        print(f"  API调用率: {api_calls/total_segments*100:.1f}%")
        
        return results

    def test_connection(self) -> bool:
        """测试API连接"""
        try:
            result = self.detect_and_correct_text_errors("测试连接")
            return 'error' not in result
        except Exception:
            return False
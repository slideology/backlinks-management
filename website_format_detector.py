#!/usr/bin/env python3
"""
网站格式检测器
用于分析外链目标网站的技术特征和内容格式支持
"""

import requests
import re
from urllib.parse import urljoin, urlparse
from bs4 import BeautifulSoup
from typing import Dict, List, Optional, Tuple
import time
import random

class WebsiteFormatDetector:
    """网站格式和特征检测器"""

    def __init__(self):
        self.session = requests.Session()
        # 模拟真实浏览器的 User-Agent
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        })

        # 常见的富文本编辑器特征
        self.rich_editors = {
            'tinymce': ['tinymce', 'mce-'],
            'ckeditor': ['ckeditor', 'cke_'],
            'froala': ['froala'],
            'summernote': ['note-editor', 'summernote'],
            'quill': ['ql-editor', 'quill']
        }

        # 常见的评论系统
        self.comment_systems = {
            'disqus': ['disqus'],
            'facebook': ['fb-comments'],
            'wordpress': ['wp-comment', 'comment-form'],
            'custom': ['comment', 'reply']
        }

    def analyze_website(self, url: str) -> Dict:
        """分析网站的完整信息"""
        try:
            print(f"正在分析: {url}")

            # 获取网页内容
            response = self.session.get(url, timeout=10)
            response.raise_for_status()

            soup = BeautifulSoup(response.content, 'html.parser')

            analysis = {
                'url': url,
                'status_code': response.status_code,
                'title': self._get_title(soup),
                'supported_formats': self._detect_supported_formats(soup, response.text),
                'comment_system': self._detect_comment_system(soup),
                'rich_editor': self._detect_rich_editor(soup, response.text),
                'forms': self._analyze_forms(soup),
                'captcha_detected': self._detect_captcha(soup, response.text),
                'registration_required': self._check_registration_required(soup),
                'social_login': self._detect_social_login(soup),
                'content_guidelines': self._extract_content_guidelines(soup),
                'platform_type': self._identify_platform_type(soup, response.text),
                'meta_info': self._extract_meta_info(soup)
            }

            return analysis

        except Exception as e:
            return {
                'url': url,
                'error': str(e),
                'status': 'failed'
            }

    def _get_title(self, soup: BeautifulSoup) -> str:
        """获取页面标题"""
        title_tag = soup.find('title')
        return title_tag.get_text().strip() if title_tag else 'No title'

    def _detect_supported_formats(self, soup: BeautifulSoup, html_text: str) -> List[str]:
        """检测支持的链接格式"""
        formats = []

        # 检测 HTML 支持
        if self._check_html_support(soup, html_text):
            formats.append('html')

        # 检测 Markdown 支持
        if self._check_markdown_support(soup, html_text):
            formats.append('markdown')

        # 检测 BBCode 支持
        if self._check_bbcode_support(soup, html_text):
            formats.append('bbcode')

        # 默认支持纯文本
        formats.append('plain_text')

        return formats

    def _check_html_support(self, soup: BeautifulSoup, html_text: str) -> bool:
        """检测 HTML 格式支持"""
        indicators = [
            # 富文本编辑器
            'contenteditable="true"',
            'wysiwyg',
            'rich-editor',
            'html-editor',
            # 常见的 HTML 标签提示
            '&lt;a href',
            '<a href',
            'HTML tags allowed',
            'html is allowed'
        ]

        html_lower = html_text.lower()
        return any(indicator.lower() in html_lower for indicator in indicators)

    def _check_markdown_support(self, soup: BeautifulSoup, html_text: str) -> bool:
        """检测 Markdown 格式支持"""
        indicators = [
            'markdown',
            'md-editor',
            'supports markdown',
            '**bold**',
            '*italic*',
            '[link](url)',
            'markdown formatting'
        ]

        html_lower = html_text.lower()
        return any(indicator.lower() in html_lower for indicator in indicators)

    def _check_bbcode_support(self, soup: BeautifulSoup, html_text: str) -> bool:
        """检测 BBCode 格式支持"""
        indicators = [
            'bbcode',
            '[url=',
            '[b]',
            '[i]',
            'BB Code',
            'bbcode is on',
            '[url]http'
        ]

        html_lower = html_text.lower()
        return any(indicator.lower() in html_lower for indicator in indicators)

    def _detect_comment_system(self, soup: BeautifulSoup) -> Dict:
        """检测评论系统类型"""
        for system_name, selectors in self.comment_systems.items():
            for selector in selectors:
                if soup.find(attrs={'class': lambda x: x and selector in x.lower() if x else False}) or \
                   soup.find(attrs={'id': lambda x: x and selector in x.lower() if x else False}):
                    return {'type': system_name, 'detected': True}

        # 检查是否有评论表单
        comment_forms = soup.find_all('form')
        for form in comment_forms:
            form_text = str(form).lower()
            if any(word in form_text for word in ['comment', 'reply', 'message']):
                return {'type': 'custom', 'detected': True, 'has_form': True}

        return {'type': 'unknown', 'detected': False}

    def _detect_rich_editor(self, soup: BeautifulSoup, html_text: str) -> Dict:
        """检测富文本编辑器"""
        html_lower = html_text.lower()

        for editor_name, selectors in self.rich_editors.items():
            for selector in selectors:
                if selector.lower() in html_lower:
                    return {'type': editor_name, 'detected': True}

        return {'type': 'none', 'detected': False}

    def _analyze_forms(self, soup: BeautifulSoup) -> List[Dict]:
        """分析页面上的表单"""
        forms = []

        for form in soup.find_all('form'):
            form_info = {
                'action': form.get('action', ''),
                'method': form.get('method', 'GET').upper(),
                'fields': [],
                'has_file_upload': False,
                'has_captcha': False
            }

            # 分析表单字段
            for input_tag in form.find_all(['input', 'textarea', 'select']):
                field_info = {
                    'type': input_tag.get('type', input_tag.name),
                    'name': input_tag.get('name', ''),
                    'required': input_tag.has_attr('required'),
                    'placeholder': input_tag.get('placeholder', '')
                }
                form_info['fields'].append(field_info)

                # 检查文件上传
                if input_tag.get('type') == 'file':
                    form_info['has_file_upload'] = True

                # 检查验证码字段
                field_name = input_tag.get('name', '').lower()
                if any(captcha_word in field_name for captcha_word in ['captcha', 'recaptcha', 'verification']):
                    form_info['has_captcha'] = True

            forms.append(form_info)

        return forms

    def _detect_captcha(self, soup: BeautifulSoup, html_text: str) -> Dict:
        """检测验证码"""
        captcha_indicators = [
            'recaptcha',
            'g-recaptcha',
            'captcha',
            'hcaptcha',
            'cloudflare',
            'verification code',
            'security question'
        ]

        html_lower = html_text.lower()
        detected_types = []

        for indicator in captcha_indicators:
            if indicator in html_lower:
                detected_types.append(indicator)

        return {
            'detected': len(detected_types) > 0,
            'types': detected_types
        }

    def _check_registration_required(self, soup: BeautifulSoup) -> bool:
        """检查是否需要注册"""
        indicators = [
            'login required',
            'sign in to comment',
            'register to post',
            'member login',
            'authentication required'
        ]

        page_text = soup.get_text().lower()
        return any(indicator in page_text for indicator in indicators)

    def _detect_social_login(self, soup: BeautifulSoup) -> List[str]:
        """检测社交登录选项"""
        social_platforms = ['facebook', 'google', 'twitter', 'github', 'linkedin', 'discord']
        detected = []

        for platform in social_platforms:
            if soup.find(attrs={'class': lambda x: x and platform in x.lower() if x else False}) or \
               soup.find('a', href=lambda x: x and platform in x.lower() if x else False):
                detected.append(platform)

        return detected

    def _extract_content_guidelines(self, soup: BeautifulSoup) -> Dict:
        """提取内容指导原则"""
        guidelines_text = ""

        # 查找可能包含指导原则的区域
        guidelines_selectors = [
            'guidelines', 'rules', 'policy', 'terms',
            'posting-rules', 'community-guidelines'
        ]

        for selector in guidelines_selectors:
            elements = soup.find_all(attrs={'class': lambda x: x and selector in x.lower() if x else False})
            for element in elements:
                guidelines_text += element.get_text() + " "

        return {
            'found': len(guidelines_text.strip()) > 0,
            'content': guidelines_text.strip()[:500]  # 限制长度
        }

    def _identify_platform_type(self, soup: BeautifulSoup, html_text: str) -> str:
        """识别平台类型"""
        html_lower = html_text.lower()

        platform_indicators = {
            'wordpress': ['wp-content', 'wordpress', 'wp-includes'],
            'drupal': ['drupal', 'sites/all/modules'],
            'joomla': ['joomla', 'option=com_'],
            'phpbb': ['phpbb', 'viewtopic.php'],
            'vbulletin': ['vbulletin', 'showthread.php'],
            'discourse': ['discourse', 'ember-app'],
            'reddit': ['reddit.com'],
            'disqus': ['disqus.com'],
            'medium': ['medium.com'],
            'blogger': ['blogger.com', 'blogspot.com']
        }

        for platform, indicators in platform_indicators.items():
            if any(indicator in html_lower for indicator in indicators):
                return platform

        return 'unknown'

    def _extract_meta_info(self, soup: BeautifulSoup) -> Dict:
        """提取元数据信息"""
        meta_info = {}

        # 获取描述
        description = soup.find('meta', attrs={'name': 'description'})
        if description:
            meta_info['description'] = description.get('content', '')

        # 获取关键词
        keywords = soup.find('meta', attrs={'name': 'keywords'})
        if keywords:
            meta_info['keywords'] = keywords.get('content', '')

        # 获取语言
        html_tag = soup.find('html')
        if html_tag:
            meta_info['language'] = html_tag.get('lang', 'unknown')

        return meta_info

    def batch_analyze(self, urls: List[str], delay: Tuple[int, int] = (1, 3)) -> Dict:
        """批量分析网站"""
        results = {}

        for i, url in enumerate(urls, 1):
            print(f"处理 {i}/{len(urls)}: {url}")

            try:
                results[url] = self.analyze_website(url)

                # 随机延迟避免被封
                if i < len(urls):
                    sleep_time = random.randint(delay[0], delay[1])
                    time.sleep(sleep_time)

            except Exception as e:
                results[url] = {'error': str(e), 'status': 'failed'}
                print(f"分析失败: {url} - {e}")

        return results

def main():
    """主函数 - 测试用例"""
    detector = WebsiteFormatDetector()

    # 测试 URL
    test_urls = [
        "https://www.reddit.com/r/test/",
        "https://stackoverflow.com/questions/ask",
        "https://github.com/new/issue"
    ]

    for url in test_urls:
        print(f"\\n{'='*50}")
        result = detector.analyze_website(url)

        if 'error' not in result:
            print(f"网站: {result['title']}")
            print(f"支持格式: {', '.join(result['supported_formats'])}")
            print(f"评论系统: {result['comment_system']}")
            print(f"需要注册: {result['registration_required']}")
            print(f"验证码: {result['captcha_detected']}")
            print(f"平台类型: {result['platform_type']}")
        else:
            print(f"分析失败: {result['error']}")

if __name__ == "__main__":
    main()
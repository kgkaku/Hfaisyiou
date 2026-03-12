#!/usr/bin/env python3
"""
Toffee M3U Playlist Generator - With Auto Proxy Fetching from ProxyDB
"""

import requests
import json
import time
import re
import os
import sys
import logging
from datetime import datetime
from typing import Dict, List, Optional
from urllib3.exceptions import InsecureRequestWarning

# Suppress SSL warnings
requests.packages.urllib3.disable_warnings(InsecureRequestWarning)

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('logs/generator.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

class ToffeeAPI:
    def __init__(self):
        self.session = requests.Session()
        
        # Browser headers
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'application/json, text/plain, */*',
            'Accept-Language': 'en-US,en;q=0.9',
            'Origin': 'https://www.toffeelive.com',
            'Referer': 'https://www.toffeelive.com/',
        })
        
        self.base_url = "https://content-prod.services.toffeelive.com/toffee/BD/DK/web"
        self.entitlement_url = "https://entitlement-prod.services.toffeelive.com/toffee/BD/DK/web"
        self.cdn_domains = [
            "bldcmprod-cdn.toffeelive.com",
            "bidcmprod-cdn.toffeelive.com"
        ]
        
        self.channels = []
        self.channel_ids = set()
        self.content_ids = set()
        self.start_time = datetime.now()
        self.working_proxy = None
        
        # Known rail IDs
        self.known_rail_ids = [
            "911e8f640af3a8892b628714d4acc133",
            "55fdb2bedaca2de399b470fb0ce14117",
            "08d90cecf964eb9a5f6be2e1887066fd",
            "cb7ea308e7742680ea8df1aae153bc9b",
            "cceb01a3ecb01516539b0adad38c1400",
            "be7d42854f019db42fbc22153674b888",
            "84a2451df95d2eb3d2b0d09c5fc34fb1",
            "36eff4e5ed817e63c4a0859a0e11f1d5",
        ]
    
    def fetch_bd_proxies(self):
        """Fetch Bangladesh proxies from ProxyDB"""
        logger.info("🌐 Fetching Bangladesh proxies from ProxyDB...")
        proxies = []
        
        try:
            # Method 1: Parse the HTML page you found
            response = self.session.get(
                "https://proxydb.net/?country=BD",
                timeout=10,
                headers={'User-Agent': 'Mozilla/5.0'}
            )
            
            if response.status_code == 200:
                # Parse the proxy list from HTML
                import re
                # Find all IP:port combinations in the table
                pattern = r'(\d+\.\d+\.\d+\.\d+)\s+(\d+)\s+(HTTP|HTTPS|SOCKS5)'
                matches = re.findall(pattern, response.text)
                
                for ip, port, proxy_type in matches:
                    proxies.append({
                        'ip': ip,
                        'port': int(port),
                        'type': proxy_type.lower(),
                        'source': 'proxydb'
                    })
                
                logger.info(f"✅ Found {len(proxies)} proxies from ProxyDB")
            
            # Method 2: Try the API if available
            # Some proxy sites have APIs, but ProxyDB might not
            
        except Exception as e:
            logger.error(f"Error fetching proxies: {e}")
        
        return proxies
    
    def test_proxy(self, proxy):
        """Test if proxy works with Toffee"""
        proxy_url = f"{proxy['type']}://{proxy['ip']}:{proxy['port']}"
        
        # Create a new session for testing
        test_session = requests.Session()
        test_session.proxies = {'http': proxy_url, 'https': proxy_url}
        test_session.headers.update(self.session.headers)
        
        try:
            # Test with a simple request to Toffee
            response = test_session.get(
                "https://www.toffeelive.com",
                timeout=5,
                verify=False
            )
            
            if response.status_code == 200:
                logger.info(f"✅ Proxy {proxy['ip']}:{proxy['port']} works")
                return True
        except Exception as e:
            logger.debug(f"Proxy {proxy['ip']} failed: {e}")
        
        return False
    
    def find_working_proxy(self, proxies):
        """Find first working proxy from list"""
        logger.info("🔍 Testing proxies...")
        
        # Sort by type preference (HTTPS first, then HTTP)
        sorted_proxies = sorted(proxies, 
                              key=lambda x: 0 if x['type'] == 'https' else 1)
        
        for proxy in sorted_proxies[:20]:  # Test first 20
            if self.test_proxy(proxy):
                self.working_proxy = proxy
                proxy_url = f"{proxy['type']}://{proxy['ip']}:{proxy['port']}"
                self.session.proxies = {'http': proxy_url, 'https': proxy_url}
                
                # Save working proxy for next run
                with open('working_proxy.json', 'w') as f:
                    json.dump(proxy, f)
                
                return True
        
        # Try loading last working proxy
        try:
            if os.path.exists('working_proxy.json'):
                with open('working_proxy.json', 'r') as f:
                    last_proxy = json.load(f)
                    if self.test_proxy(last_proxy):
                        self.working_proxy = last_proxy
                        proxy_url = f"{last_proxy['type']}://{last_proxy['ip']}:{last_proxy['port']}"
                        self.session.proxies = {'http': proxy_url, 'https': proxy_url}
                        logger.info("✅ Using last working proxy")
                        return True
        except:
            pass
        
        logger.warning("⚠️ No working proxy found")
        return False
    
    def discover_all_rails(self):
        """Get all rail/collection IDs"""
        logger.info("🔍 Discovering content rails...")
        rail_ids = self.known_rail_ids.copy()
        
        try:
            response = self.session.get("https://www.toffeelive.com", timeout=10, verify=False)
            if response.status_code == 200:
                import re
                found_ids = re.findall(r'[a-f0-9]{32}', response.text)
                rail_ids.extend([fid for fid in found_ids if fid not in rail_ids])
        except Exception as e:
            logger.debug(f"Error scraping main page: {e}")
        
        logger.info(f"📋 Found {len(rail_ids)} potential rail IDs")
        return rail_ids
    
    def get_rail_contents(self, rail_id: str) -> List[Dict]:
        """Get contents of a specific rail"""
        url = f"{self.base_url}/rail/generic/editorial-dynamic/{rail_id}"
        
        try:
            response = self.session.get(url, timeout=10, verify=False)
            if response.status_code == 200:
                data = response.json()
                
                items = []
                if 'list' in data:
                    items = data['list']
                elif 'items' in data:
                    items = data['items']
                elif isinstance(data, list):
                    items = data
                
                if items:
                    logger.info(f"✅ Rail {rail_id[:8]}... has {len(items)} items")
                return items
        except Exception as e:
            logger.debug(f"Error getting rail {rail_id}: {e}")
        
        return []
    
    def extract_channels_from_rail(self, items: List[Dict]):
        """Extract channel information from rail items"""
        for item in items:
            if not isinstance(item, dict):
                continue
            
            title = item.get('title', '') or item.get('name', '') or item.get('displayName', '')
            
            if not title or len(title) < 3:
                continue
            
            # Generate channel ID from title (no hardcoded mapping)
            channel_id = title.lower()
            channel_id = re.sub(r'[^a-z0-9]', '_', channel_id)
            channel_id = re.sub(r'_+', '_', channel_id).strip('_')
            
            if channel_id not in self.channel_ids:
                self.channel_ids.add(channel_id)
                
                channel = {
                    'id': item.get('id', ''),
                    'name': title,
                    'channel_id': channel_id,
                    'image': item.get('image', '') or item.get('thumbnail', '') or item.get('poster', ''),
                    'type': item.get('type', ''),
                    'stream_url': f"https://bldcmprod-cdn.toffeelive.com/cdn/live/{channel_id}/playlist.m3u8",
                }
                
                self.channels.append(channel)
                logger.info(f"📺 Found: {title}")
    
    def get_playlist_with_cookie(self, channel: Dict) -> Optional[str]:
        """Fetch playlist and extract cookie"""
        
        # Try both CDN domains
        for domain in self.cdn_domains:
            stream_url = channel['stream_url'].replace('bldcmprod-cdn', domain)
            
            try:
                headers = {
                    'User-Agent': 'Toffee (Linux;Android 14)',
                    'Accept': '*/*',
                }
                
                response = self.session.get(
                    stream_url, 
                    headers=headers, 
                    timeout=10, 
                    verify=False,
                    allow_redirects=True
                )
                
                if response.status_code == 200:
                    match = re.search(r'#EXTHTTP:\{"cookie":"(Edge-Cache-Cookie=[^"]+)"\}', response.text)
                    if match:
                        cookie = match.group(1)
                        logger.info(f"🔑 Got cookie for {channel['name']}")
                        return cookie
                        
            except Exception as e:
                logger.debug(f"Failed for {channel['name']}: {e}")
                continue
        
        return None
    
    def generate_m3u_playlist(self, filename='playlists/toffee_playlist.m3u'):
        """Generate M3U playlist with real cookies"""
        logger.info(f"📝 Generating playlist: {filename}")
        
        now = datetime.now()
        date_str = now.strftime("%Y_%m_%d")
        time_str = now.strftime("%H:%M:%S")
        
        os.makedirs('playlists', exist_ok=True)
        
        successful = 0
        failed = 0
        
        with open(filename, 'w', encoding='utf-8') as f:
            # Header
            f.write("#EXTM3U\n")
            f.write(f"# By kgkaku\n")
            f.write(f"# Scrapped by @kgkaku\n")
            f.write(f"# Scrapped on {date_str} {time_str}\n")
            f.write(f"# Credit must be given. Otherwise it will be closed.\n")
            f.write("\n")
            
            # Welcome sample
            f.write('#EXTINF:-1 tvg-name="kgkaku" group-title="kgkaku" tvg-logo="https://www.solidbackgrounds.com/images/1920x1080/1920x1080-bright-green-solid-color-background.jpg",Welcome\n')
            f.write('http://commondatastorage.googleapis.com/gtv-videos-bucket/sample/BigBuckBunny.mp4\n')
            f.write("\n")
            
            # Process channels
            total = len(self.channels)
            for i, channel in enumerate(self.channels, 1):
                logger.info(f"🔄 Processing {i}/{total}: {channel['name']}")
                
                cookie = self.get_playlist_with_cookie(channel)
                
                if cookie:
                    successful += 1
                    
                    # Prepare tags
                    tags = [
                        f'tvg-id="{channel["channel_id"]}"',
                        f'tvg-name="{channel["name"]}"',
                        f'group-title="{channel.get("type", "Toffee")}"'
                    ]
                    if channel.get('image'):
                        tags.append(f'tvg-logo="{channel["image"]}"')
                    
                    f.write(f'#EXTINF:-1 {" ".join(tags)},{channel["name"]}\n')
                    f.write(f'#EXTVLCOPT:http-user-agent=Toffee (Linux;Android 14)\n')
                    f.write(f'#EXTHTTP:{{"cookie":"{cookie}"}}\n')
                    f.write(f'{channel["stream_url"]}\n\n')
                else:
                    failed += 1
                    logger.warning(f"❌ No cookie for {channel['name']}")
            
            # Footer
            f.write(f"# Working channels: {successful}/{total}\n")
            f.write(f"# Proxy used: {self.working_proxy['ip'] if self.working_proxy else 'None'}\n")
            f.write(f"# Generated on {date_str} {time_str}\n")
        
        logger.info(f"✅ Playlist generated: {filename}")
        logger.info(f"📊 Working: {successful}, Failed: {failed}")
        
        return successful, failed

def main():
    """Main function"""
    logger.info("="*60)
    logger.info("🎬 TOFFEE PLAYLIST GENERATOR - WITH AUTO PROXY")
    logger.info("="*60)
    
    api = ToffeeAPI()
    
    # Step 1: Fetch Bangladesh proxies
    proxies = api.fetch_bd_proxies()
    
    if not proxies:
        logger.error("❌ No proxies found!")
        return
    
    # Step 2: Find a working proxy
    if not api.find_working_proxy(proxies):
        logger.error("❌ No working proxy found!")
        return
    
    logger.info(f"✅ Using proxy: {api.working_proxy['ip']}:{api.working_proxy['port']}")
    
    # Step 3: Discover channels (now through proxy)
    rail_ids = api.discover_all_rails()
    
    for rail_id in rail_ids:
        items = api.get_rail_contents(rail_id)
        if items:
            api.extract_channels_from_rail(items)
        time.sleep(0.5)
    
    logger.info(f"📺 Total channels: {len(api.channels)}")
    
    # Step 4: Generate playlist with cookies
    if api.channels:
        successful, failed = api.generate_m3u_playlist()
        
        if successful > 0:
            logger.info("✅ Success! Playlist generated with working cookies")
        else:
            logger.warning("⚠️ No cookies obtained - proxy might not be from Bangladesh")
    else:
        logger.error("❌ No channels found!")

if __name__ == "__main__":
    main()

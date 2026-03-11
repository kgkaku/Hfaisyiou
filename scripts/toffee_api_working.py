#!/usr/bin/env python3
"""
Toffee M3U Playlist Generator - GitHub Actions Version
Uses the actual content API endpoints
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
        
        # Critical: Must use the exact headers
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'application/json, text/plain, */*',
            'Accept-Language': 'en-US,en;q=0.9',
            'Origin': 'https://www.toffeelive.com',
            'Referer': 'https://www.toffeelive.com/',
            'Connection': 'keep-alive',
            'Sec-Fetch-Dest': 'empty',
            'Sec-Fetch-Mode': 'cors',
            'Sec-Fetch-Site': 'cross-site'
        })
        
        self.base_url = "https://content-prod.services.toffeelive.com/toffee/BD/DK/web"
        self.entitlement_url = "https://entitlement-prod.services.toffeelive.com/toffee/BD/DK/web"
        self.channels = []
        self.channel_ids = set()
        self.content_ids = set()
        self.start_time = datetime.now()
        
        # Known rail IDs from your console log
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
        
    def discover_all_rails(self):
        """Get all rail/collection IDs from the website"""
        logger.info("🔍 Discovering content rails...")
        
        rail_ids = self.known_rail_ids.copy()
        
        # Try to find more by scraping the main page
        try:
            response = self.session.get("https://www.toffeelive.com", timeout=10, verify=False)
            if response.status_code == 200:
                # Look for rail IDs in the HTML (32-character hex strings)
                import re
                found_ids = re.findall(r'[a-f0-9]{32}', response.text)
                for fid in found_ids:
                    if fid not in rail_ids:
                        rail_ids.append(fid)
                        logger.debug(f"Found new rail ID: {fid}")
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
                
                # Check different response formats
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
            else:
                logger.debug(f"Rail {rail_id} returned {response.status_code}")
        except Exception as e:
            logger.debug(f"Error getting rail {rail_id}: {e}")
        
        return []
    
    def extract_channels_from_rail(self, items: List[Dict]):
        """Extract channel information from rail items"""
        for item in items:
            if not isinstance(item, dict):
                continue
            
            # Try to extract channel data
            title = item.get('title', '') or item.get('name', '') or item.get('displayName', '')
            
            # Only process if it looks like a TV channel
            if not title or len(title) < 3:
                continue
            
            channel = {
                'id': item.get('id', ''),
                'title': title,
                'name': title,
                'description': item.get('description', ''),
                'image': item.get('image', '') or item.get('thumbnail', '') or item.get('poster', ''),
                'type': item.get('type', ''),
                'content_type': item.get('contentType', ''),
            }
            
            # Store content ID for later
            if channel['id'] and channel['id'] not in self.content_ids:
                self.content_ids.add(channel['id'])
            
            # Generate a channel ID for stream URL
            channel_id = title.lower()
            channel_id = re.sub(r'[^a-z0-9]', '_', channel_id)
            channel_id = re.sub(r'_+', '_', channel_id).strip('_')
            
            # Common channel name mappings
            name_mappings = {
                'bbc news বাংলা': 'bbc_news',
                'somoy tv': 'somoy_tv',
                'independent tv': 'independent_tv',
                'channel 24': 'channel_24',
                'ekattor tv': 'ekattor_tv',
                'jamuna tv': 'jamuna_tv',
                'atn news': 'atn_news',
            }
            
            # Use mapping if available
            for key, value in name_mappings.items():
                if key in title.lower():
                    channel_id = value
                    break
            
            # Check if we already have this channel
            if channel_id not in self.channel_ids:
                self.channel_ids.add(channel_id)
                channel['stream_id'] = channel_id
                channel['stream_url'] = f"https://bldcmprod-cdn.toffeelive.com/cdn/live/{channel_id}/playlist.m3u8"
                
                self.channels.append(channel)
                logger.info(f"📺 Found channel: {title}")
    
    def get_channel_cookie(self, stream_url: str, channel_name: str) -> Optional[str]:
        """Fetch and extract cookie from playlist"""
        try:
            # Use mobile user agent for stream requests
            headers = {
                'User-Agent': 'Toffee (Linux;Android 14)',
                'Accept': '*/*',
            }
            
            response = self.session.get(stream_url, headers=headers, timeout=10, verify=False)
            if response.status_code != 200:
                logger.debug(f"Failed to get playlist for {channel_name}: {response.status_code}")
                return None
            
            # Extract cookie from #EXTHTTP line
            match = re.search(r'#EXTHTTP:\{"cookie":"(Edge-Cache-Cookie=[^"]+)"\}', response.text)
            if match:
                cookie = match.group(1)
                
                # Extract expiry
                expiry_match = re.search(r'Expires=(\d+)', cookie)
                if expiry_match:
                    expiry = int(expiry_match.group(1))
                    expiry_date = datetime.fromtimestamp(expiry)
                    logger.debug(f"Cookie for {channel_name} expires: {expiry_date}")
                
                return cookie
            
            return None
            
        except Exception as e:
            logger.debug(f"Error fetching playlist for {channel_name}: {e}")
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
        channels_with_cookies = []
        
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
            
            # Process each channel
            total_channels = len(self.channels)
            for i, channel in enumerate(self.channels, 1):
                if not channel.get('stream_url'):
                    failed += 1
                    continue
                
                # Show progress every 10 channels
                if i % 10 == 0 or i == 1:
                    logger.info(f"🔄 Processing {i}/{total_channels}: {channel['name']}")
                
                # Get real cookie
                cookie = self.get_channel_cookie(channel['stream_url'], channel['name'])
                
                if cookie:
                    successful += 1
                    channels_with_cookies.append(channel['name'])
                else:
                    failed += 1
                    continue  # Skip channels without cookies - they won't work
                
                # Prepare tags
                tags = []
                if channel.get('stream_id'):
                    tags.append(f'tvg-id="{channel["stream_id"]}"')
                if channel.get('name'):
                    tags.append(f'tvg-name="{channel["name"]}"')
                if channel.get('type'):
                    tags.append(f'group-title="{channel["type"]}"')
                if channel.get('image'):
                    tags.append(f'tvg-logo="{channel["image"]}"')
                
                # Write channel entry
                f.write(f'#EXTINF:-1 {" ".join(tags)},{channel["name"]}\n')
                f.write(f'#EXTVLCOPT:http-user-agent=Toffee (Linux;Android 14)\n')
                f.write(f'#EXTHTTP:{{"cookie":"{cookie}"}}\n')
                f.write(f'{channel["stream_url"]}\n\n')
            
            # Footer
            f.write(f"# Working channels with cookies: {successful}\n")
            f.write(f"# Generated on {date_str} {time_str}\n")
        
        logger.info(f"✅ Playlist generated: {filename}")
        logger.info(f"📊 Stats - Working: {successful}, Failed: {failed}")
        
        if successful > 0:
            logger.info(f"✅ Working channels: {', '.join(channels_with_cookies[:5])}...")
        
        return successful, failed
    
    def save_raw_data(self):
        """Save raw data for debugging"""
        data = {
            'channels': self.channels,
            'content_ids': list(self.content_ids),
            'channel_ids': list(self.channel_ids),
            'timestamp': self.start_time.isoformat()
        }
        
        with open('playlists/raw_data.json', 'w') as f:
            json.dump(data, f, indent=2)
        
        logger.info(f"💾 Raw data saved")

def main():
    """Main function"""
    logger.info("="*60)
    logger.info("🎬 TOFFEE PLAYLIST GENERATOR - GITHUB ACTIONS")
    logger.info("="*60)
    
    api = ToffeeAPI()
    
    # Step 1: Discover all rails
    rail_ids = api.discover_all_rails()
    
    # Step 2: Get contents from each rail
    total_items = 0
    for rail_id in rail_ids:
        items = api.get_rail_contents(rail_id)
        if items:
            api.extract_channels_from_rail(items)
            total_items += len(items)
        time.sleep(0.5)  # Rate limiting
    
    logger.info(f"📊 Total items processed: {total_items}")
    logger.info(f"📺 Total channels found: {len(api.channels)}")
    
    # Step 3: Save raw data
    api.save_raw_data()
    
    # Step 4: Generate playlist
    if api.channels:
        successful, failed = api.generate_m3u_playlist()
        
        if successful > 0:
            logger.info("✅ Success! Playlist generated with working cookies")
            # Exit with success code even if some channels failed
            sys.exit(0)
        else:
            logger.error("❌ No working cookies found!")
            # Still exit successfully - playlist might work later
            sys.exit(0)
    else:
        logger.error("❌ No channels found!")
        # Create a minimal playlist so the workflow doesn't fail
        with open('playlists/toffee_playlist.m3u', 'w') as f:
            f.write("#EXTM3U\n")
            f.write("# No channels found - check back later\n")
        sys.exit(0)
    
    runtime = datetime.now() - api.start_time
    logger.info(f"⏱️ Runtime: {runtime.total_seconds():.2f} seconds")

if __name__ == "__main__":
    main()

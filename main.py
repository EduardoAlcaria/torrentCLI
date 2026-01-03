import libtorrent as lt
import time
import sys
import warnings
from pathlib import Path
from rich.progress import Progress, BarColumn, TextColumn, DownloadColumn, TransferSpeedColumn
from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel

warnings.filterwarnings("ignore", category=DeprecationWarning)

class TorrentDownloader:

    
    def __init__(self, download_dir='downloads'):
        self.console = Console()
        self.download_dir = Path(download_dir)
        self.session = None

        
    def initialize_session(self):
      
        self.session = lt.session()
        self.session.listen_on(6881, 6891)
        self.session.start_dht()
        self.download_dir.mkdir(exist_ok=True)
        
    def add_torrent(self, magnet_link):
      
        if not magnet_link:
            raise ValueError("Magnet link cannot be empty")
            
       
        
        params = lt.parse_magnet_uri(magnet_link)
        params.save_path = str(self.download_dir.resolve())
        
        handle = self.session.add_torrent(params)
        

        while not handle.has_metadata():
            time.sleep(0.1)
            
        return handle
        
    def download(self, handle):
     
        torrent_info = handle.torrent_file()
        torrent_name = torrent_info.name() if torrent_info else handle.status().name
        
    
        progress = Progress(
            TextColumn("[bold blue]{task.description}"),
            BarColumn(),
            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            DownloadColumn(),
            TransferSpeedColumn(),
            console=self.console
        )
        
        task_id = progress.add_task(f"Downloading: {torrent_name}", total=100)
        
     
        panel = Panel(
            f"[bold green]Downloading:[/] {torrent_name}",
            title="[bold cyan]Torrent Download[/]",
            border_style="green"
        )
        self.console.print(panel)
        
 
        with Live(progress, console=self.console, refresh_per_second=10):
            while True:
                status = handle.status()
                
                if status.is_seeding:
                    break
                    
                progress_pct = status.progress * 100
                progress.update(task_id, completed=progress_pct)
                
              
                if status.error:
                    raise RuntimeError(f"Download error: {status.error}")
                    
                time.sleep(0.1)
        
        self.console.print(f"\n[bold green]✓ Download complete:[/] {torrent_name}")
        self.console.print(f"[cyan]Saved to:[/] {self.download_dir / torrent_name}")
        
    def fetch_magnet(self, magnet_link):
        
        try:
            self.initialize_session()
            handle = self.add_torrent(magnet_link)
            self.download(handle)
        except KeyboardInterrupt:
            self.console.print("\n[yellow]Download cancelled by user[/]")
            sys.exit(0)
        except Exception as e:
            self.console.print(f"\n[bold red]Error:[/] {str(e)}")
            sys.exit(1)


def main():

    if len(sys.argv) < 2:
        Console().print("[bold red]Error:[/] Please provide a magnet link")
        Console().print("[cyan]Usage:[/] python script.py <magnet_link>")
        sys.exit(1)
        
    magnet_link = sys.argv[1]
    
    downloader = TorrentDownloader()
    downloader.fetch_magnet(magnet_link)


if __name__ == '__main__':
    main()
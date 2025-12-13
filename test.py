import libtorrent as lt
import time
import sys
import os
from rich.progress import Progress
from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel


console = Console()

status = console.status('Downloading');

progress = Progress(transient=True);


def featch_magnet(magnet_link='', magnet_array = []):


    ses = lt.session()

    ses.listen_on(6881, 6891)
        
    ses.start_dht()

    os.makedirs('downloads', exist_ok=True)

    
    handles = list()

    if (magnet_array is not None):
        for magnet in magnet_array:
            print(f"\n{'033[1;33m'}magnet {magnet}{'\033[m'}")
            params = lt.parse_magnet_uri(magnet)
            params.save_path = os.path.abspath('downloads')
            handles.append(ses.add_torrent(params))


    
    


    status.update(f"[bold green] Downloading")

    
    task_id = progress.add_task(f"[bold green]")
    
    sessions = list()
    
    for h in handles:
        torrent_name = h.status().name
        sessions.append(torrent_name)

    panel = Panel(f"[bold green] Dowloading{sessions}")
    console.print(panel)

    with Live(Panel(Group(status, progress)), console=console, refresh_per_second=5):
        for h in handles:
            s = h.status()

            while not s.is_seeding:
                
                

                pct = int(s.progress * 100)
                print(f'Download debug for the {s.name} {s.progress}%')

                progress.update(task_id=task_id, completed=pct)

                time.sleep(0.3)

        status.update(f"[bold green]Done Downloading {handles}")

if __name__ == '__main__':
    magnet = sys.argv[1];
    magnet_array = list()


    print(magnet.count(','))
    if (magnet.count(',') == 0):
        featch_magnet(magnet)

    else:
        for e in magnet.split(','):
            magnet_array.append(e)
            print(e)
        featch_magnet(magnet_array=magnet_array)            
    
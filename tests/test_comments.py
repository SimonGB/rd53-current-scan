import csv
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from scan import parser, run
from station import run_mapped
from mapping import default_profile


class CommentsTests(unittest.TestCase):
    def test_comments_round_trip(self):
        note = 'Wiring changed, probes x10\nTemperature: 23 °C'
        with tempfile.TemporaryDirectory() as d:
            args = parser().parse_args(['--simulate', '--start', '.01', '--stop', '.01',
                '--step', '.01', '--voltage-limit', '3.5', '--max-current', '.05',
                '--output', d, '--comments', note])
            with patch('scan.plot'), patch('station.plot_mapped'):
                runs = [(run(args), 'results.csv'),
                        (run_mapped(args, default_profile()), 'measurements.csv')]
            for out, name in runs:
                self.assertEqual((out/'comments.txt').read_text(), note)
                metadata = json.loads((out/'metadata.json').read_text())
                self.assertEqual(metadata.get('comments', metadata.get('settings', {}).get('comments')), note)
                with (out/name).open(newline='') as f:
                    self.assertTrue(all(row['comments'] == note for row in csv.DictReader(f)))

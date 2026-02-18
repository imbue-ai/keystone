#!/usr/bin/env perl
use strict;
use warnings;
use IO::Select;
use IO::Handle;
use POSIX qw(strftime);

use Getopt::Long;

my $logfile;
my $stamp_stdout = 0;
my $stamp_stderr = 0;
Getopt::Long::Configure("require_order");
GetOptions(
    'logfile=s' => \$logfile,
    'stamp-stdout' => \$stamp_stdout,
    'stamp-stderr' => \$stamp_stderr,
) or die "Usage: $0 [--logfile FILE] [--stamp-stdout] [--stamp-stderr] command [args...]\n";

die "Usage: $0 [--logfile FILE] [--stamp-stdout] [--stamp-stderr] command [args...]\n" unless @ARGV;

my @cmd = @ARGV;

my $log;
if (defined $logfile) {
    open $log, ">>", $logfile or die "Cannot open $logfile: $!";
    $log->autoflush(1);
}

STDOUT->autoflush(1);
STDERR->autoflush(1);

pipe my $out_r, my $out_w or die "pipe: $!";
pipe my $err_r, my $err_w or die "pipe: $!";

my $start_time = time();

my $pid = fork();
die "fork: $!" unless defined $pid;

if ($pid == 0) {
    close $out_r;
    close $err_r;
    open STDOUT, ">&", $out_w or die "dup stdout: $!";
    open STDERR, ">&", $err_w or die "dup stderr: $!";
    exec @cmd or die "exec failed: $!";
}

close $out_w;
close $err_w;

my $sel = IO::Select->new();
$sel->add($out_r, $err_r);

sub iso_ts { strftime("%Y-%m-%dT%H:%M:%S%z", localtime) }

my $have_hires = eval { require Time::HiRes; Time::HiRes->import('time'); 1 };

sub elapsed {
    $have_hires ? sprintf("%.3f", time() - $start_time) : sprintf("%d", time() - $start_time)
}

while (my @ready = $sel->can_read) {
    for my $fh (@ready) {
        my $line = <$fh>;
        if (defined $line) {
            chomp $line;
            my $type = ($fh == $out_r) ? "STDOUT" : "STDERR";
            my $ts = iso_ts();
            my $el = elapsed();
            my $log_msg = "$ts\t$el\t$type\t$line\n";
            print $log $log_msg if defined $log;

            my $stamp = ($type eq "STDOUT" && $stamp_stdout) || ($type eq "STDERR" && $stamp_stderr);
            if ($stamp) {
                if ($type eq "STDOUT") {
                    print STDOUT $log_msg;
                } else {
                    print STDERR $log_msg;
                }
            } else {
                if ($type eq "STDOUT") {
                    print STDOUT "$line\n";
                } else {
                    print STDERR "$line\n";
                }
            }
        } else {
            $sel->remove($fh);
            close $fh;
        }
    }
}

waitpid($pid, 0);
exit($? >> 8);

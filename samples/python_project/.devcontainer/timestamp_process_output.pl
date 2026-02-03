#!/usr/bin/env perl
use strict;
use warnings;
use IO::Select;
use IO::Handle;
use POSIX qw(strftime);
use Getopt::Long;

my $logfile;
Getopt::Long::Configure("require_order");
GetOptions('logfile=s' => \$logfile) or die "Usage: $0 [--logfile FILE] command [args...]\n";

die "Usage: $0 [--logfile FILE] command [args...]\n" unless @ARGV;

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

while (my @ready = $sel->can_read) {
    for my $fh (@ready) {
        my $line = <$fh>;
        if (defined $line) {
            chomp $line;
            my $type = ($fh == $out_r) ? "STDOUT" : "STDERR";
            my $ts = iso_ts();
            my $msg = "$ts\t$type\t$line\n";
            print $msg;
            print $log $msg if defined $log;
        } else {
            $sel->remove($fh);
            close $fh;
        }
    }
}

waitpid($pid, 0);
exit($? >> 8);

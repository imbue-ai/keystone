#!/usr/bin/env bash
snapshots=$(docker images --format '{{.Repository}} {{.ID}}' | awk '$1 ~ /-snapshot$/ {print $2}')

docker system df -v | awk '/^REPOSITORY/{in_images=1;next}/^Containers:/{in_images=0}
in_images && NF>0 && !/-snapshot[[:space:]]/ && $NF==0{print}' > /tmp/non_snap_images

total=0
while read -r line; do
  imgid=$(echo "$line" | awk '{print $(NF-4)}')
  uniqsize=$(echo "$line" | awk '{print $(NF-1)}')
  num=$(echo "$uniqsize" | sed 's/[^0-9.]//g')
  unit=$(echo "$uniqsize" | sed 's/[0-9.]//g')
  if [[ $unit == GB ]]; then val=$num
  elif [[ $unit == MB ]]; then val=$(awk "BEGIN{print $num/1024}")
  elif [[ $unit == kB || $unit == KB ]]; then val=$(awk "BEGIN{print $num/1024/1024}")
  else val=$num; fi

  found=false
  for sid in $snapshots; do
    if docker history "$sid" --format '{{.ID}}' 2>/dev/null | grep -q "$imgid"; then
      echo "Skipping $imgid (present in snapshot history)"
      found=true
      break
    fi
  done
  if ! $found; then
    echo "$line"
    total=$(awk -v a="$total" -v b="$val" 'BEGIN{print a+b}')
  fi
done < /tmp/non_snap_images

printf "\nTotal unique size (non-snapshot, no containers, not in snapshot history): %.3f GB\n" "$total"

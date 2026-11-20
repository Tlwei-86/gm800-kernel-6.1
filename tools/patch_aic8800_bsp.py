#!/usr/bin/env python3
"""
Patch AIC8800 BSP Driver against Stack Smashing Panic
------------------------------------------------------
Bug: In aic8800_bsp, struct aicbt_patch_info_t has only 32 bytes of storage for
firmware patch tables. When firmware returns >4 entries, aicbt_patch_info_unpack
blindly copies memory, overflowing the stack buffer and destroying the stack canary
([SP + 0x28]), causing:
  "Kernel panic - not syncing: stack-protector: Kernel stack is corrupted in: aicbt_patch_trap_data_load"

Fix:
1. In aic_bsp_driver.h: Add uint32_t reserved[64] (256 bytes extra capacity) to struct aicbt_patch_info_t.
2. In aic_bsp_driver.c: Enforce bounds checking with max_copy in aicbt_patch_info_unpack.
"""

import os
import sys

def patch_file(path, old_pattern, new_replacement, desc):
    if not os.path.exists(path):
        print(f"[-] Target file not found: {path}")
        return False
    
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        content = f.read()
    
    if new_replacement.strip() in content:
        print(f"[+] Already patched: {desc} in {path}")
        return True
    
    if old_pattern not in content:
        print(f"[-] Target pattern not found for {desc} in {path}!")
        return False
    
    new_content = content.replace(old_pattern, new_replacement, 1)
    with open(path, "w", encoding="utf-8") as f:
        f.write(new_content)
    
    print(f"[+] Successfully applied patch: {desc} in {path}")
    return True

def main():
    root = sys.argv[1] if len(sys.argv) > 1 else "linux-src"
    
    bsp_dir = os.path.join(root, "drivers", "net", "wireless", "aic8800_sdio", "aic8800_bsp")
    if not os.path.exists(bsp_dir):
        # Fallback search
        candidate = None
        for dirpath, _, filenames in os.walk(root):
            if "aic_bsp_driver.c" in filenames and "aic_bsp_driver.h" in filenames:
                candidate = dirpath
                break
        if candidate:
            bsp_dir = candidate
        else:
            print(f"[-] Could not find aic8800_bsp directory under {root}")
            sys.exit(1)
    
    h_file = os.path.join(bsp_dir, "aic_bsp_driver.h")
    c_file = os.path.join(bsp_dir, "aic_bsp_driver.c")
    
    # 1. Header patch
    old_h = "uint32_t adid_flag;\n};"
    new_h = "uint32_t adid_flag;\n\tuint32_t reserved[64]; /* Extended buffer to prevent stack canary overflow */\n};"
    
    ok_h = patch_file(h_file, old_h, new_h, "aicbt_patch_info_t buffer expansion")
    
    # 2. C source patch
    old_c = """int aicbt_patch_info_unpack(struct aicbt_patch_info_t *patch_info, struct aicbt_patch_table *head_t)
{
    if (AICBT_PT_INF == head_t->type) {
        patch_info->info_len = head_t->len;
        if(patch_info->info_len == 0)
            return 0;
        memcpy(&patch_info->adid_addrinf, head_t->data, patch_info->info_len * sizeof(uint32_t) * 2);
    }
    return 0;
}"""

    new_c = """int aicbt_patch_info_unpack(struct aicbt_patch_info_t *patch_info, struct aicbt_patch_table *head_t)
{
    if (AICBT_PT_INF == head_t->type) {
        size_t max_copy = sizeof(struct aicbt_patch_info_t) - offsetof(struct aicbt_patch_info_t, adid_addrinf);
        size_t copy_len;
        patch_info->info_len = head_t->len;
        if(patch_info->info_len == 0)
            return 0;
        copy_len = head_t->len * sizeof(uint32_t) * 2;
        if (copy_len > max_copy) {
            printk(KERN_WARNING "aicbt: patch table len %u exceeds info buf (%zu > %zu), capping\\n",
                   head_t->len, copy_len, max_copy);
            copy_len = max_copy;
        }
        memcpy(&patch_info->adid_addrinf, head_t->data, copy_len);
    }
    return 0;
}"""

    ok_c = patch_file(c_file, old_c, new_c, "aicbt_patch_info_unpack bounds check")
    
    if ok_h and ok_c:
        print("[*] AIC8800 BSP driver stack canary protection successfully deployed!")
        sys.exit(0)
    else:
        print("[-] Patching failed!")
        sys.exit(1)

if __name__ == "__main__":
    main()

"""Assertions for private paths created under the current Windows account."""
import os
import stat


def assert_private_path(testcase, path, posix_mode):
    if os.name == 'nt':
        assert_inherited_private_acl(testcase, path)
    else:
        testcase.assertEqual(stat.S_IMODE(path.stat().st_mode), posix_mode)


def assert_inherited_private_acl(testcase, path):
    if os.name != 'nt':
        raise AssertionError('Windows ACL assertion used outside Windows')
    import win32api
    import win32con
    import win32file
    import win32security as security
    from hey_my_buddy.paths import native_path

    handle = win32file.CreateFile(str(native_path(path)), win32con.READ_CONTROL | 0x80,
                                  win32con.FILE_SHARE_READ | win32con.FILE_SHARE_WRITE |
                                  win32con.FILE_SHARE_DELETE, None, win32con.OPEN_EXISTING,
                                  0x00200000 | 0x02000000, None)  # OPEN_REPARSE_POINT | BACKUP_SEMANTICS
    try:
        attributes = win32file.GetFileInformationByHandleEx(
            handle, win32file.FileAttributeTagInfo)['FileAttributes']
        testcase.assertFalse(attributes & 0x1, f'READONLY attribute on {path}')
        testcase.assertFalse(attributes & 0x400, f'reparse point on {path}')
        token = security.OpenProcessToken(win32api.GetCurrentProcess(), win32con.TOKEN_QUERY)
        try:
            user = security.ConvertSidToStringSid(
                security.GetTokenInformation(token, security.TokenUser)[0])
        finally:
            token.Close()
        descriptor = security.GetSecurityInfo(handle, security.SE_FILE_OBJECT,
                                               security.OWNER_SECURITY_INFORMATION |
                                               security.DACL_SECURITY_INFORMATION)
        control, _ = descriptor.GetSecurityDescriptorControl()
        owner = security.ConvertSidToStringSid(descriptor.GetSecurityDescriptorOwner())
        testcase.assertIn(owner, {user, 'S-1-5-32-544'}, str(path))
        testcase.assertTrue(control & security.SE_DACL_PRESENT, str(path))
        testcase.assertFalse(control & security.SE_DACL_PROTECTED, str(path))
        dacl = descriptor.GetSecurityDescriptorDacl()
        testcase.assertIsNotNone(dacl, str(path))
        allowed = {user, 'S-1-5-18', 'S-1-5-32-544', 'S-1-3-4'}
        observed_aces = []
        for index in range(dacl.GetAceCount()):
            ace = dacl.GetAce(index)
            testcase.assertEqual(len(ace), 3, (path, index, ace))
            testcase.assertEqual(len(ace[0]), 2, (path, index, ace))
            testcase.assertEqual(ace[0][0], security.ACCESS_ALLOWED_ACE_TYPE,
                                 (path, index, ace))
            testcase.assertTrue(ace[0][1] & security.INHERITED_ACE,
                                (path, index, ace))
            trustee = security.ConvertSidToStringSid(ace[2])
            testcase.assertIn(trustee, allowed, (path, index, ace))
            observed_aces.append((ace[0][0], ace[0][1], ace[1], trustee))
        return owner, tuple(observed_aces)
    finally:
        handle.Close()

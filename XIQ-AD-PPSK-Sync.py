#!/usr/bin/env python3
import json
import requests
import sys
import os
import logging
import smtplib,ssl
from ldap3 import Server, Connection, ALL, NTLM, SUBTREE
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email.mime.text import MIMEText
from email.utils import formatdate
from email import encoders

####################################
# written by:   Tim Smith
# e-mail:       tismith@extremenetworks.com
# date:         1 July 2024
# version:      2.0.7.1 
# branch:       snakedogipa
#               - Adding Email support
#               - sAMAAccountName instead of username and password
#               - additional error code 546, 66082
####################################


# Global Variables - ADD CORRECT VALUES
server_name = "enter the server name/ IP"
domain_name = "enter the domain name"
user_name = "enter AD username"
password = " enter AD password"

#AD MaxPageSize 
page = 1000
#AD Filter to search
AD_Filter = ""

#XIQ_username = "enter your ExtremeCloudIQ Username"
#XIQ_password = "enter your ExtremeCLoudIQ password"
####OR###
## TOKEN permission needs - enduser, pcg:key
XIQ_token = "****"

group_roles = [
    # AD GROUP Distinguished Name, XIQ group ID
    ("AD Group Distinguished Name", "XIQ User Group ID"),
    ("AD Group Distinguished Name", "XIQ User Group ID")
]

PCG_Enable = False

PCG_Maping = {
    "XIQ User Group ID" : {
        "UserGroupName": "XIQ User Group Name",
        "policy_id": "Network Policy ID associated with PCG",
         "policy_name": "Network Policy name associated with PCG"
    }
}

# SMTP Settings
tolist = ["email address","mulitple seperated by commas"] 
sender_email = 'sender email address'
email_subject = 'XIQ AD PPSK Sync Report'
smtp_server = 'Outgoing Email (SMTP) server'
smtp_port = 25


error_msg = ''

#-------------------------
# logging
PATH = os.path.dirname(os.path.abspath(__file__))
logging.basicConfig(
    filename='{}/XIQ-AD-PPSK-sync.log'.format(PATH),
    filemode='a',
    level=os.environ.get("LOGLEVEL", "INFO"),
    format= '%(asctime)s: %(name)s - %(levelname)s - %(message)s', datefmt='%Y-%m-%d %H:%M:%S'
)
# userAccountControl codes used for disabled accounts
ldap_disable_codes = ['514','546','642','66050','66178','66082']

URL = "https://api.extremecloudiq.com"
headers = {"Accept": "application/json", "Content-Type": "application/json"}


def sendmail(fromaddr, toaddr, email_body, email_subject, smtpsrv, smtpport):
		# Build the email
		toHeader = ", ".join(toaddr)
		msg = MIMEText(email_body)
		msg['Subject'] = email_subject
		msg['From'] = fromaddr
		msg['To'] = toHeader

		try:
				# The actual mail send
				server = smtplib.SMTP(smtpsrv, smtpport)
				server.ehlo()
				server.sendmail(fromaddr, toaddr, msg.as_string())
				server.quit()
				#debug_print "email sent: %s" % fromaddr

		except Exception as e:
				logmsg = "Something went wrong when sending the email {}".format(fromaddr)
				logging.error(logmsg)
				logging.error(e)
				raise TypeError(f"{logmsg}\n   {e}")

def sendErrorMsg(msg):
    global error_msg
    email_msg = error_msg + '\n' + msg
    print(email_msg)
    print("sending email")
    try:
        sendmail(sender_email, tolist , email_msg, email_subject, smtp_server, smtp_port)
    except TypeError as e:
        logging.error(e)
        print(e)        
    print("email sent")
    print("script exiting....")
    raise SystemExit
    


def retrieveADUsers(ad_group):
    #Building search base from fqdn
    subdir_list = domain_name.split('.')
    tdl = subdir_list[-1]
    subdir_list = subdir_list[:-1]
    if subdir_list:
        SearchBase = 'DC=' + ',DC='.join(subdir_list) + ',DC=' + tdl
    else:
        SearchBase = 'DC=' + tdl
    ad_result = []
    try:
        server = Server(server_name, get_info=ALL)
        conn = Connection(server, user='{}\\{}'.format(domain_name, user_name), password=password, authentication=NTLM, auto_bind=True)
        conn.search(
            search_base= SearchBase,
            search_filter='(&(objectClass=user)(memberof:1.2.840.113556.1.4.1941:={}){})'.format(ad_group,AD_Filter),
            search_scope=SUBTREE,
            attributes = ['objectClass', 'userAccountControl', 'sAMAccountName', 'name', 'mail'],
            paged_size = page)
        ad_result.extend(conn.entries)
        print(f"completed page of AD Users. Total Users collected is {len(ad_result)}")
        cookie = conn.result['controls']['1.2.840.113556.1.4.319']['value']['cookie']
        while cookie:
            conn.search(
                search_base= SearchBase,
                search_filter='(&(objectClass=user)(memberof:1.2.840.113556.1.4.1941:={}){})'.format(ad_group,AD_Filter),
                search_scope=SUBTREE,
                attributes = ['objectClass', 'userAccountControl', 'sAMAccountName', 'name', 'mail'],
                paged_size = page,
                paged_cookie = cookie)
            ad_result.extend(conn.entries)
            print(f"completed page of AD Users. Total Users collected is {len(ad_result)}")
            cookie = conn.result['controls']['1.2.840.113556.1.4.319']['value']['cookie']
        conn.unbind()
        return ad_result
    except:
        log_msg = f"Unable to reach server {server_name}"
        logging.error(log_msg)
        print(log_msg)
        sendErrorMsg(log_msg)
    


def getAccessToken(XIQ_username, XIQ_password):
    url = URL + "/login"
    payload = json.dumps({"username": XIQ_username, "password": XIQ_password})
    response = requests.post(url, headers=headers, data=payload)
    if response is None:
        log_msg = "ERROR: Not able to login into ExtremeCloudIQ - no response!"
        logging.error(log_msg)
        raise TypeError(log_msg)
    if response.status_code != 200:
        log_msg = f"Error getting access token - HTTP Status Code: {str(response.status_code)}"
        logging.error(f"{log_msg}")
        logging.warning(f"\t\t{response}")
        raise TypeError(log_msg)
    data = response.json()

    if "access_token" in data:
        #print("Logged in and Got access token: " + data["access_token"])
        headers["Authorization"] = "Bearer " + data["access_token"]
        return 0

    else:
        log_msg = "Unknown Error: Unable to gain access token"
        logging.warning(log_msg)
        raise TypeError(log_msg)


def createPPSKuser(name,user_name, mail, usergroupID):
    url = URL + "/endusers"

    payload = json.dumps({"user_group_id": usergroupID ,"name": name,"user_name": user_name,"password": "", "email_address": mail, "email_password_delivery": mail})

    response = requests.post(url, headers=headers, data=payload, verify=True)
    if response is None:
        log_msg = "Error adding PPSK user - no response!"
        logging.error(log_msg)
        raise TypeError(log_msg)

    elif response.status_code != 200:
        log_msg = f"Error adding PPSK user {name} - HTTP Status Code: {str(response.status_code)}"
        logging.error(log_msg)
        logging.warning(f"\t\t{response.json()}")
        raise TypeError(log_msg)

    elif response.status_code ==200:
        logging.info(f"successfully created PPSK user {name}")
        print(f"successfully created PPSK user {name}")
        return True




def retrievePPSKUsers(pageSize, usergroupID):
    page = 1
    pageCount = 1
    firstCall = True

    ppskUsers = []

    while page <= pageCount:
        url = URL + "/endusers?page=" + str(page) + "&limit=" + str(pageSize) + "&user_group_ids=" + usergroupID

        # Get the next page of the ppsk users
        response = requests.get(url, headers=headers, verify = True)
        if response is None:
            log_msg = "Error retrieving PPSK users from XIQ - no response!"
            logging.error(log_msg)
            raise TypeError(log_msg)

        elif response.status_code != 200:
            log_msg = f"Error retrieving PPSK users from XIQ - HTTP Status Code: {str(response.status_code)}"
            logging.error(log_msg)
            logging.warning(f"\t\t{response.json()}")
            raise TypeError(log_msg)

        rawList = response.json()
        ppskUsers = ppskUsers + rawList['data']

        if firstCall == True:
            pageCount = rawList['total_pages']
        print(f"completed page {page} of {rawList['total_pages']} collecting PPSK Users")
        page = rawList['page'] + 1 
    return ppskUsers



def deleteUser(userId):
    url = URL + "/endusers/" + str(userId)
    response = requests.delete(url, headers=headers, verify=True)
    if response is None:
        log_msg = f"Error deleting PPSK user {userId} - no response!"
        logging.error(log_msg)
        raise TypeError(log_msg)
    elif response.status_code != 200:
        log_msg = f"Error deleting PPSK user {userId} - HTTP Status Code: {str(response.status_code)}"
        logging.error(log_msg)
        logging.warning(f"\t\t{response.json()}")
        raise TypeError(log_msg)
    elif response.status_code == 200:
        return 'Success', str(userId)
    

def addUserToPcg(policy_id, name, email, user_group_name):
    url = URL + "/pcgs/key-based/network-policy-" + str(policy_id) + "/users"
    payload = json.dumps({
                  "users": [
                    {
                      "name": name,
                      "email": email,
                      "user_group_name": user_group_name
                    }
                  ]
                })
    response = requests.post(url, headers=headers, data=payload, verify=True)
    if response is None:
        log_msg = f"- no response!"
        logging.error(log_msg)
        raise TypeError(log_msg)
    elif response.status_code != 202:
        log_msg = f"HTTP Status Code: {str(response.status_code)}"
        logging.error(log_msg)
        logging.warning(f"\t\t{response}")
        raise TypeError(log_msg)
    elif response.status_code == 202:
        return 'Success'

def retrievePCGUsers(policy_id):
    url = URL + "/pcgs/key-based/network-policy-" + str(policy_id) + "/users"
    response = requests.get(url, headers=headers, verify = True)
    if response is None:
        log_msg = f"Error retrieving PCG users for policy id {policy_id} from XIQ - no response!"
        logging.error(log_msg)
        raise TypeError(log_msg)
    elif response.status_code != 200:
        log_msg = f"Error retrieving PCG users for policy id {policy_id} from XIQ - HTTP Status Code: {str(response.status_code)}"
        logging.error(log_msg)
        logging.warning(f"\t\t{response.json()}")
        raise TypeError(log_msg)
    rawList = response.json()
    return rawList

def deletePCGUsers(policy_id, userId):
    url = URL + "/pcgs/key-based/network-policy-" + str(policy_id) + "/users"
    payload = json.dumps({
                    "user_ids": [
                                    userId
                                ]
                })
    response = requests.delete(url, headers=headers, data=payload, verify = True)
    if response is None:
        log_msg = f"Error deleting PPSK user {userId} - no response!"
        logging.error(log_msg)
        raise TypeError(log_msg)
    elif response.status_code != 202:
        log_msg = f"Error deleting PPSK user {userId} - HTTP Status Code: {str(response.status_code)}"
        logging.error(log_msg)
        logging.warning(f"\t\t{response}")
        raise TypeError(log_msg)
    elif response.status_code == 202:
        return 'Success'


def main():
    global error_msg
    if 'XIQ_token' not in globals():
        try:
            login = getAccessToken(XIQ_username, XIQ_password)
        except TypeError as e:
            print(e)
            sendErrorMsg(e)
        except:
            log_msg = "Unknown Error: Failed to generate token"
            logging.error(log_msg)
            print(log_msg)
            sendErrorMsg(log_msg)     
    else:
        headers["Authorization"] = "Bearer " + XIQ_token
 
    ListOfADgroups, ListOfXIQUserGroups = zip(*group_roles)

    # Collect PSK users
    ppsk_users = []
    for usergroupID in ListOfXIQUserGroups:
        try:
            ppsk_users += retrievePPSKUsers(100,usergroupID)
        except TypeError as e:
            print(e)
            # not having ppsk will break later line - if not any(d['name'] == name for d in ppsk_users):
            sendErrorMsg(str(e))
        except:
            log_msg = ("Unknown Error: Failed to retrieve users from XIQ")
            logging.error(log_msg)
            print(log_msg)
            # not having ppsk will break later line - if not any(d['name'] == name for d in ppsk_users):
            sendErrorMsg(log_msg)
    log_msg = ("Successfully parsed " + str(len(ppsk_users)) + " XIQ users")
    logging.info(log_msg)
    print(f"{log_msg}\n")

    # Collect LDAP Users
    ldap_users = {}
    ldap_capture_success = True
    for ad_group, xiq_user_role in group_roles:
        ad_result = retrieveADUsers(ad_group)
        for ldap_entry in ad_result:
            if str(ldap_entry.sAMAccountName) not in ldap_users:
                try:
                    ldap_users[str(ldap_entry.sAMAccountName)] = {
                        "userAccountControl": str(ldap_entry.userAccountControl),
                        "email": str(ldap_entry.mail),
                        "username": str(ldap_entry.sAMAccountName),
                        "name": str(ldap_entry.name),
                        "xiq_role": xiq_user_role
                    }
                except:
                    log_msg = (f"Unexpected error: {sys.exc_info()[0]}")
                    logging.error(log_msg)
                    print(log_msg)
                    error_msg = error_msg + log_msg + '\n'
                    logging.warning("User info was not captured from Active Directory")
                    logging.warning(f"{ldap_entry}")
                    # not having ppsk will break later line - for name, details in ldap_users.items():
                    ldap_capture_success = False
                    continue
            else:
                log_msg = (f"User {ldap_entry.sAMAccountName} already exists in data, skipping user")
                logging.warning(log_msg)
                logging.info(f"{ldap_entry}")
                error_msg = error_msg + log_msg + '\n'


    log_msg = "Successfully parsed " + str(len(ldap_users)) + " LDAP users"
    logging.info(log_msg)
    print(f"{log_msg}\n")

    # Track Error counts
    ppsk_create_error = 0
    pcg_create_error = 0
    ppsk_del_error = 0
    pcg_del_error = 0

    # Create PPSK Users
    ldap_disabled = []
    for name, details in ldap_users.items():
        user_created = False
        if details['email'] == '[]':
            log_msg = (f"User {name} doesn't have an email set and will not be created in xiq")
            logging.warning(log_msg)
            print(log_msg)
            error_msg = error_msg + log_msg + '\n'
            continue
        if not any(d['user_name'] == details['username'] for d in ppsk_users) and not any(d == details['userAccountControl'] for d in ldap_disable_codes):
            try:
                user_created = createPPSKuser(details['name'], details['username'], details["email"], details['xiq_role'])
            except TypeError as e:
                log_msg = f"failed to create {details['username']}: {e}"
                logging.error(log_msg)
                print(log_msg)
                error_msg = error_msg + log_msg + '\n'
                ppsk_create_error+=1
            except:
                log_msg = f"Unknown Error: Failed to create user {details['username']} - {details['email']}"
                logging.error(log_msg)
                print(log_msg)
                error_msg = error_msg + log_msg + '\n'
                ppsk_create_error+=1
            if PCG_Enable == True and user_created == True and str(details['xiq_role']) in PCG_Maping:
                ## add user to PCG if PCG is Enabled
                policy_id = PCG_Maping[details['xiq_role']]['policy_id']
                policy_name = PCG_Maping[details['xiq_role']]['policy_name']
                user_group_name = PCG_Maping[details['xiq_role']]['UserGroupName']
                email = details["email"]
                result = ''
                try:
                    result = addUserToPcg(policy_id, details['username'], email, user_group_name)
                except TypeError as e:
                    log_msg = f"failed to add {details['username']} to pcg {policy_name}: {e}"
                    logging.error(log_msg)
                    print(log_msg)
                    error_msg = error_msg + log_msg + '\n'
                    pcg_create_error+=1
                except:
                    log_msg = f"Unknown Error: Failed to add user {details['username']} - {details['email']} to pcg {policy_name}"
                    logging.error(log_msg)
                    print(log_msg)
                    error_msg = error_msg + log_msg + '\n'
                    pcg_create_error+=1
                if result == 'Success':
                    log_msg = f"User {details['username']} - was successfully add to pcg {policy_name}."
                    logging.info(log_msg)
                    print(log_msg)
                    pcg_create_error+=1

        elif any(d == details['userAccountControl'] for d in ldap_disable_codes):
            ldap_disabled.append(name)
    
    # Remove disabled accounts from ldap users
    for name in ldap_disabled:
        logging.info(f"User {name} is is disabled in AD with disable code {ldap_users[name]['userAccountControl']}.")
        del ldap_users[name]
    
    if PCG_Enable == True:
        pcg_capture_success = True
        # Collect PCG Users if PCG is Enabled
        PCGUsers = []
        for policy in PCG_Maping:
            policy_id = PCG_Maping[policy]['policy_id']

            try:
                PCGUsers += retrievePCGUsers(policy_id)
            except TypeError as e:
                print(e)
                error_msg = error_msg + log_msg + '\n'
                pcg_capture_success = False
                # not having ppsk will break later line - if not any(d['name'] == name for d in ppsk_users):
            except:
                log_msg = ("Unknown Error: Failed to retrieve users from XIQ")
                logging.error(log_msg)
                print(log_msg)
                error_msg = error_msg + log_msg + '\n'
                pcg_capture_success = False
                # not having ppsk will break later line - if not any(d['name'] == name for d in ppsk_users):

        log_msg = "Successfully parsed " + str(len(PCGUsers)) + " PCG users"
        logging.info(log_msg)
        print(f"{log_msg}\n")

    if ldap_capture_success:
        for x in ppsk_users:
            user_group_id = x['user_group_id']
            email = x['email_address']
            xiq_id = x['id']
            # check if any xiq user is not included in active ldap users
            if not any(d['email'] == email for d in ldap_users.values()):
                if PCG_Enable == True and str(user_group_id) in PCG_Maping:
                    if pcg_capture_success == False:
                        log_msg = f"Due to PCG read failure, user {email} cannot be deleted"
                        logging.error(log_msg)
                        print(log_msg)
                        error_msg = error_msg + log_msg + '\n'
                        ppsk_del_error+=1
                        pcg_del_error+=1
                        continue
                # not having ppsk will break later line - if not any(d['name'] == name for d in ppsk_users):
                    # If PCG is Enabled, Users need to be deleted from PCG group before they can be deleted from User Group
                    if any(d['email'] == email for d in PCGUsers):
                        # Find specific PCG user and get the user id
                        PCGUser = (list(filter(lambda PCGUser: PCGUser['email'] == email, PCGUsers)))[0]
                        pcg_id = PCGUser['id']
                        for PCG_Map in PCG_Maping.values():
                            if PCG_Map['UserGroupName'] == PCGUser['user_group_name']:
                                policy_id = PCG_Map['policy_id']
                                policy_name = PCG_Map['policy_name']
                        result = ''
                        try:
                            result = deletePCGUsers(policy_id, pcg_id)
                        except TypeError as e:
                            logmsg = f"Failed to delete user {email} from PCG group {policy_name} with error {e}"
                            logging.error(logmsg)
                            print(logmsg)
                            error_msg = error_msg + log_msg + '\n'
                            ppsk_del_error+=1
                            pcg_del_error+=1
                            continue
                        except:
                            log_msg = f"Unknown Error: Failed to delete user {email} from pcg group {policy_name}"
                            logging.error(log_msg)
                            print(log_msg)
                            error_msg = error_msg + log_msg + '\n'
                            ppsk_del_error+=1
                            pcg_del_error+=1
                            continue
                        if result == 'Success':
                            log_msg = f"User {email} - {pcg_id} was successfully deleted from pcg group {policy_name}."
                            logging.info(log_msg)
                            print(log_msg)
                        else:
                            log_msg = f"User {email} - {pcg_id} was not successfully deleted from pcg group {policy_name}. User cannot be deleted from the PPSK Group."
                            logging.error(log_msg)
                            print(log_msg)
                            error_msg = error_msg + log_msg + '\n'
                            ppsk_del_error+=1
                            pcg_del_error+=1 
                            continue
                result = ''
                try:
                    result, userid = deleteUser(xiq_id)
                except TypeError as e:
                    logmsg = f"Failed to delete user {email}  with error {e}"
                    logging.error(logmsg)
                    print(logmsg)
                    error_msg = error_msg + log_msg + '\n'
                    ppsk_del_error+=1
                    continue
                except:
                    log_msg = f"Unknown Error: Failed to delete user {email} "
                    logging.error(log_msg)
                    print(log_msg)
                    error_msg = error_msg + log_msg + '\n'
                    ppsk_del_error+=1
                    continue
                if result == 'Success':
                    log_msg = f"User {email} - {userid} was successfully deleted."
                    logging.info(log_msg)
                    print(log_msg)
                else:
                    log_msg = f"User {email} - {userid} did not successfully delete from the PPSK Group."
                    logging.error(log_msg)
                    print(log_msg)
                    error_msg = error_msg + log_msg + '\n'
                    ppsk_del_error+=1

        if ppsk_create_error:
            log_msg = f"There were {ppsk_create_error} errors creating PPSK users on this run."
            logging.info(log_msg)
            print(log_msg)
        if pcg_create_error:
            log_msg = f"There were {pcg_create_error} errors creating PCG users on this run."
            logging.info(log_msg)
            print(log_msg)
        if ppsk_del_error:
            log_msg = f"There were {ppsk_del_error} errors deleting PPSK users on this run."
            logging.info(log_msg)
            print(log_msg)
        if pcg_del_error:
            log_msg = f"There were {pcg_del_error} errors deleting PCG users on this run."
            logging.info(log_msg)
            print(log_msg)

    else:
        log_msg = "No users will be deleted from XIQ because of the error(s) in reading ldap users"
        logging.warning(log_msg)
        print(log_msg)
        error_msg = error_msg + log_msg + '\n'

    if error_msg:
         
        print("sending email")

        try:
            sendmail(sender_email, tolist , error_msg, email_subject, smtp_server, smtp_port)
        except TypeError as e:
            logging.error(e)
            print(e)        
        print("email sent")


if __name__ == '__main__':
	main()

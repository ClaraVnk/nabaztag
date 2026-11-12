#ifndef MY_SIMUNET_H_
#define MY_SIMUNET_H_

int simunetinit();
int checkNetworkEvents();

int tcpservercreate(int port);
int tcpopen(char* dstip,int dstport);
int tcpclose(int i);
int tcpsend(int i,char* msg, int len);
int tcpevent(int eventId, int wParam, int lParam);
void tcpenable(int i,int enable);

int udpcreate(int port);
int udpclose(int port);
int udpsend(int localport,char* dstip,int dstport,char* msg, int len);
int udpevent(int eventId, int wParam, int lParam);

#endif // ! MY_SIMUNET_H_